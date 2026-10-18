"""Translate a SubtitleDocument with any :class:`Translator`.

1. Each cue is cut into *units*: runs of text with one style and speaker.
   A line break inside a unit is soft (the translation is re-wrapped), except
   before a line starting with a dialogue dash, which starts a new unit.
2. Cues without letters (e.g. a lone ".") are copied unchanged.
3. Cues are sent in batches, with a few preceding cues (and their
   translations) as context. Answers are validated per cue: right id, right
   number of segments. Bad cues are re-requested; if a cue keeps failing it is
   translated as one joined segment, and if even that fails it is left as is.
4. Translated units are laid out again with :func:`wrap.flow`: line breaks
   between units (speaker/style changes, dialogue dashes) are kept, and the
   text between them is wrapped to the maximum line length.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from subtitle_translate.model import Cue, Line, Segment, SubtitleDocument
from subtitle_translate.translate.base import (
    ContextCue,
    CueText,
    InvalidResponseError,
    TranslationRequest,
    Translator,
)
from subtitle_translate.wrap import DEFAULT_MAX_LINE_LENGTH, flow

_DIALOGUE_DASH = re.compile(r"^\s*[-–—]\s")
_LEAD = re.compile(r"^\s+")
_TRAIL = re.compile(r"\s+$")

Progress = Callable[[int, int], None]


@dataclass
class _Unit:
    segments: list[Segment]  # original segments (same style and voice)
    breaks_before: bool  # starts on a new line (hard break)
    translation: str | None = None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)

    @property
    def translatable(self) -> bool:
        return any(ch.isalpha() for ch in self.text)


@dataclass
class _CueJob:
    index: int
    cue: Cue
    units: list[_Unit]
    attempts: int = 0
    joined: bool = False  # fallback: all text in one segment

    @property
    def request_id(self) -> str:
        return str(self.index + 1)

    @property
    def to_translate(self) -> list[_Unit]:
        return [u for u in self.units if u.translatable]

    def source_segments(self) -> list[str]:
        texts = [u.text.strip() for u in self.to_translate]
        return [" ".join(texts)] if self.joined else texts


@dataclass
class TranslationResult:
    document: SubtitleDocument
    warnings: list[str] = field(default_factory=list)


def _units(cue: Cue) -> list[_Unit]:
    units: list[_Unit] = []
    for li, line in enumerate(cue.lines):
        for si, seg in enumerate(line.segments):
            new_line = li > 0 and si == 0
            hard = new_line and _DIALOGUE_DASH.match(line.text) is not None
            prev = units[-1] if units else None
            same = prev and (prev.segments[-1].style_id, prev.segments[-1].voice) == (seg.style_id, seg.voice)
            if prev and same and not hard:
                if new_line:
                    seg = seg.model_copy(update={"text": " " + seg.text.lstrip()})
                    prev.segments[-1] = prev.segments[-1].model_copy(
                        update={"text": prev.segments[-1].text.rstrip()})
                prev.segments.append(seg)
            else:
                units.append(_Unit([seg], breaks_before=new_line))
    return units


def _layout(job: _CueJob, max_len: int) -> list[Line]:
    """Rebuild the cue's lines from translated units."""
    groups: list[list[Segment]] = []
    for unit in job.units:
        if unit.translation is None:
            segs = [s.model_copy() for s in unit.segments]
        else:
            first = unit.segments[0]
            lead = " " if _LEAD.match(unit.text) else ""
            trail = " " if _TRAIL.search(unit.text) else ""
            segs = [Segment(text=f"{lead}{unit.translation}{trail}", style_id=first.style_id, voice=first.voice)]
        if unit.breaks_before or not groups:
            groups.append([])
        groups[-1].extend(segs)
    lines: list[Line] = []
    for group in groups:
        lines.extend(flow(group, 1, max_len))
    return lines


def _apply(job: _CueJob, translated: list[str]) -> None:
    units = job.to_translate
    if job.joined:
        units[0].translation = translated[0].strip()
        for unit in units[1:]:
            unit.translation = ""
    else:
        for unit, text in zip(units, translated):
            unit.translation = text.strip()


def translate_document(
    doc: SubtitleDocument,
    translator: Translator,
    target_language: str,
    *,
    source_language: str | None = None,
    batch_size: int = 40,
    context: int = 5,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
    max_attempts: int = 3,
    progress: Progress | None = None,
) -> TranslationResult:
    """Return a translated copy of ``doc`` (the input is not modified)."""
    doc = doc.model_copy(deep=True)
    source_language = source_language or doc.language
    jobs = [_CueJob(i, cue, _units(cue)) for i, cue in enumerate(doc.cues)]
    todo = [j for j in jobs if j.to_translate]
    warnings: list[str] = []
    done_jobs: list[_CueJob] = []
    total = len(todo)
    if progress:
        progress(0, total)

    pending = list(todo)
    while pending:
        batch = pending[:batch_size] if not pending[0].attempts else pending[:1]
        pending = pending[len(batch):]
        request = TranslationRequest(
            source_language=source_language,
            target_language=target_language,
            context=_context(done_jobs, context),
            cues=[CueText(id=j.request_id, segments=j.source_segments()) for j in batch],
        )
        try:
            answers = {c.id: c.segments for c in translator.translate(request).cues}
        except InvalidResponseError:
            answers = {}
        retry: list[_CueJob] = []
        for job in batch:
            got = answers.get(job.request_id)
            if got is not None and len(got) == len(job.source_segments()):
                _apply(job, got)
                done_jobs.append(job)
                if progress:
                    progress(len(done_jobs), total)
                continue
            job.attempts += 1
            if job.attempts < max_attempts:
                retry.append(job)
            elif not job.joined and len(job.to_translate) > 1:
                job.joined, job.attempts = True, 1
                retry.append(job)
                warnings.append(f"cue {job.cue.id or job.request_id}: segments could not be kept apart; "
                                "translated as one block")
            else:
                warnings.append(f"cue {job.cue.id or job.request_id}: translation failed; left untranslated")
                done_jobs.append(job)
                if progress:
                    progress(len(done_jobs), total)
        # Retried cues go first, one per request, so a bad batch cannot fail again as a whole.
        pending = retry + pending

    for job in todo:
        if any(u.translation is not None for u in job.units):
            job.cue.lines = _layout(job, max_line_length)
    doc.language = target_language
    return TranslationResult(document=doc, warnings=warnings)


def _context(done: list[_CueJob], n: int) -> list[ContextCue]:
    recent = sorted(done, key=lambda j: j.index)[-n:] if n else []
    return [
        ContextCue(
            id=j.request_id,
            segments=[u.text.strip() for u in j.to_translate],
            translation=[u.translation for u in j.to_translate if u.translation is not None] or None,
        )
        for j in recent
    ]
