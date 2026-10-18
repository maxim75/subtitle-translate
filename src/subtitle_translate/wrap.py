"""Line layout for translated subtitles.

Translations are usually longer or shorter than the original, so text has to
be laid out again. Two operations, both preserving each word's segment (style
and speaker):

* :func:`flow` lays styled segments out over a given number of lines
  (used to put a translated segment back over the lines it originally spanned);
* :func:`wrap_lines` splits only lines longer than the maximum.

Lines are balanced (the longest line is kept as short as possible), breaks at
a change of segment (e.g. a speaker change) are preferred, segments and cues
are never merged, and words are never split. Han and Kana text, which has no
spaces, may break between characters.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

from subtitle_translate.model import Line, Segment

DEFAULT_MAX_LINE_LENGTH = 42

_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_TOKEN = re.compile(rf"\s+|[{_CJK}]|[^\s{_CJK}]+")
_IS_CJK = re.compile(rf"[{_CJK}]")
# Above this many candidate layouts fall back to greedy filling.
_MAX_COMBINATIONS = 5_000


@dataclass
class _Token:
    text: str
    seg: int  # index of the source segment


@dataclass
class _Chunk:
    """Unbreakable run of tokens plus the separator that precedes it."""

    sep: list[_Token]  # whitespace dropped when a line breaks here ([] = CJK break)
    tokens: list[_Token]

    @property
    def width(self) -> int:
        return sum(len(t.text) for t in self.tokens)

    @property
    def sep_width(self) -> int:
        return sum(len(t.text) for t in self.sep)


def _chunks(segments: list[Segment]) -> list[_Chunk]:
    tokens = [
        _Token(m.group(0), i)
        for i, seg in enumerate(segments)
        for m in _TOKEN.finditer(seg.text)
    ]
    # Drop leading/trailing whitespace of the whole run.
    while tokens and tokens[0].text.isspace():
        tokens.pop(0)
    while tokens and tokens[-1].text.isspace():
        tokens.pop()
    chunks: list[_Chunk] = []
    sep: list[_Token] = []
    for tok in tokens:
        if tok.text.isspace():
            sep.append(tok)
        elif chunks and not sep and not (_IS_CJK.match(tok.text) or _IS_CJK.match(chunks[-1].tokens[-1].text)):
            chunks[-1].tokens.append(tok)  # e.g. "word" and "," from different segments
        else:
            chunks.append(_Chunk(sep, [tok]))
            sep = []
    return chunks


def _offsets(chunks: list[_Chunk]) -> tuple[list[int], list[int]]:
    """Start and end column of every chunk when laid out on one line."""
    starts, ends, col = [], [], 0
    for i, c in enumerate(chunks):
        col += c.sep_width if i else 0
        starts.append(col)
        col += c.width
        ends.append(col)
    return starts, ends


def _width(chunks: list[_Chunk], start: int, end: int) -> int:
    _, ends = _offsets(chunks[start:end])
    return ends[-1] if ends else 0


def _at_segment_change(chunks: list[_Chunk], i: int) -> bool:
    return chunks[i - 1].tokens[-1].seg != chunks[i].tokens[0].seg


def _best_breaks(chunks: list[_Chunk], n_lines: int) -> tuple[int, tuple[int, ...]]:
    """Break positions giving the narrowest layout over ``n_lines`` lines."""
    n = len(chunks)
    n_lines = max(1, min(n_lines, n))
    candidates = range(1, n)
    if _combinations(n - 1, n_lines - 1) > _MAX_COMBINATIONS:
        return _greedy(chunks, n_lines)
    starts, ends = _offsets(chunks)
    best: tuple | None = None
    for breaks in itertools.combinations(candidates, n_lines - 1):
        bounds = (0, *breaks, n)
        widths = [ends[b - 1] - starts[a] for a, b in itertools.pairwise(bounds)]
        mid_segment = sum(not _at_segment_change(chunks, b) for b in breaks)
        key = (max(widths), mid_segment, sum(w * w for w in widths))
        if best is None or key < best[0]:
            best = (key, breaks)
    assert best is not None
    return best[0][0], best[1]


def _combinations(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _greedy(chunks: list[_Chunk], n_lines: int) -> tuple[int, tuple[int, ...]]:
    target = _width(chunks, 0, len(chunks)) / n_lines
    breaks, start = [], 0
    for i in range(1, len(chunks)):
        if len(breaks) < n_lines - 1 and _width(chunks, start, i + 1) > target:
            breaks.append(i)
            start = i
    bounds = (0, *breaks, len(chunks))
    return max(_width(chunks, a, b) for a, b in itertools.pairwise(bounds)), tuple(breaks)


def _build(chunks: list[_Chunk], breaks: tuple[int, ...], segments: list[Segment]) -> list[Line]:
    lines = []
    for a, b in itertools.pairwise((0, *breaks, len(chunks))):
        tokens = [t for i, c in enumerate(chunks[a:b]) for t in ((c.sep if i else []) + c.tokens)]
        line, sources = Line(), []
        for tok in tokens:
            if sources and sources[-1] == tok.seg:
                line.segments[-1].text += tok.text
            else:
                src = segments[tok.seg]
                line.segments.append(Segment(text=tok.text, style_id=src.style_id, voice=src.voice))
                sources.append(tok.seg)
        lines.append(line)
    return lines


def flow(segments: list[Segment], n_lines: int, max_len: int = DEFAULT_MAX_LINE_LENGTH) -> list[Line]:
    """Lay ``segments`` out over ``n_lines`` balanced lines.

    More lines are used if needed to respect ``max_len``; fewer if there are
    not enough words. Returns at least one line.
    """
    chunks = _chunks(segments)
    if not chunks:
        return [Line()]
    k = max(1, min(n_lines, len(chunks)))
    width, breaks = _best_breaks(chunks, k)
    while width > max_len and k < len(chunks):
        k += 1
        width, breaks = _best_breaks(chunks, k)
    return _build(chunks, breaks, segments)


def wrap_lines(lines: list[Line], max_len: int = DEFAULT_MAX_LINE_LENGTH) -> list[Line]:
    """Split lines longer than ``max_len``; shorter lines are returned untouched."""
    result: list[Line] = []
    for line in lines:
        if len(line.text) <= max_len:
            result.append(line)
        else:
            result.extend(flow(line.segments, 2, max_len))
    return result
