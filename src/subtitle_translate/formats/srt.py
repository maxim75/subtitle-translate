"""SubRip (.srt) reader and writer.

Supported markup: ``<i>``, ``<b>``, ``<u>``, ``<font color="...">`` and a
leading ASS position tag ``{\\anN}``. Everything else is kept as literal text.
"""

from __future__ import annotations

import re

from subtitle_translate.colors import normalize_color
from subtitle_translate.formats.base import DroppedFeatures, FormatError, SubtitleFormat, WriteResult
from subtitle_translate.formats.registry import register
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

_TIMING = re.compile(
    r"^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})(.*)$"
)
_TAG = re.compile(r"<(/?)(i|b|u|font)(\s[^>]*)?>", re.IGNORECASE)
_FONT_COLOR = re.compile(r"""color\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
_AN = re.compile(r"^\{\\an([1-9])\}")

# Numpad layout of {\anN}: row -> vertical anchor, column -> horizontal alignment.
_AN_VERTICAL = {1: "after", 2: "after", 3: "after", 4: "center", 5: "center", 6: "center",
                7: "before", 8: "before", 9: "before"}
_AN_HORIZONTAL = {1: "left", 4: "left", 7: "left", 2: "center", 5: "center", 8: "center",
                  3: "right", 6: "right", 9: "right"}

#: White is every player's default SRT colour, so it is not written out.
_DEFAULT_COLOR = "#ffffff"


def _ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def _timestamp(ms: int) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{rest:03d}"


class _StyleTable:
    """Deduplicates styles and hands out stable ids (s1, s2, ...)."""

    def __init__(self) -> None:
        self.styles: dict[str, Style] = {}
        self._ids: dict[str, str] = {}

    def id_for(self, style: Style) -> str | None:
        if style.is_empty():
            return None
        key = style.model_dump_json()
        if key not in self._ids:
            self._ids[key] = f"s{len(self._ids) + 1}"
            self.styles[self._ids[key]] = style
        return self._ids[key]


def _parse_text(lines: list[str], table: _StyleTable) -> list[Line]:
    # Stack of (tag, Style) so closing tags restore the previous state, even across lines.
    stack: list[tuple[str, Style]] = []
    result: list[Line] = []
    for raw in lines:
        line = Line()
        pos = 0
        for m in [*_TAG.finditer(raw), None]:
            end = m.start() if m else len(raw)
            if end > pos:
                current = Style()
                for _, s in stack:
                    current = current.merged(s)
                line.segments.append(Segment(text=raw[pos:end], style_id=table.id_for(current)))
            if m is None:
                break
            pos = m.end()
            closing, tag = m.group(1) == "/", m.group(2).lower()
            if closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i]
                        break
                continue
            if tag == "font":
                color = _FONT_COLOR.search(m.group(3) or "")
                try:
                    style = Style(color=normalize_color(color.group(1))) if color else Style()
                except ValueError:
                    style = Style()
            else:
                style = Style(**{{"i": "italic", "b": "bold", "u": "underline"}[tag]: True})
            stack.append((tag, style))
        result.append(line)
    return result


@register
class SrtFormat(SubtitleFormat):
    name = "srt"
    extensions = (".srt",)

    def sniff(self, text: str) -> bool:
        head = [ln for ln in text.lstrip("﻿").splitlines()[:5] if ln.strip()]
        if not head:
            return False
        if head[0].strip().isdigit():
            head = head[1:]
        return bool(head) and bool(_TIMING.match(head[0]))

    def read(self, text: str) -> SubtitleDocument:
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        table = _StyleTable()
        regions: dict[str, Region] = {}
        cues: list[Cue] = []
        pending: list[tuple[Cue, list[str]]] = []
        line_no = 1
        for block in re.split(r"\n\s*\n", text.strip("\n")):
            lines = block.split("\n")
            start_line = line_no
            line_no += len(lines) + 1
            idx = 1 if lines[0].strip().isdigit() and len(lines) > 1 else 0
            timing = _TIMING.match(lines[idx]) if idx < len(lines) else None
            if not timing:
                # A blank line inside cue text splits the block; glue it back.
                if not pending:
                    raise FormatError(f"SRT: expected a timing line near line {start_line}")
                pending[-1][1].extend(["", *lines])
                continue
            g = timing.groups()
            ext: dict = {}
            if g[8].strip():
                ext["coordinates"] = g[8].strip()
            try:
                cue = Cue(
                    id=lines[0].strip() if idx else None,
                    start_ms=_ms(*g[0:4]),
                    end_ms=_ms(*g[4:8]),
                )
            except ValueError as e:
                raise FormatError(f"SRT: invalid timing near line {start_line}: {e}") from None
            body = lines[idx + 1:]
            if body and (an := _AN.match(body[0])):
                n = int(an.group(1))
                ext["an"] = n
                body[0] = body[0][an.end():]
                region_id = f"an{n}"
                regions.setdefault(region_id, Region(display_align=_AN_VERTICAL[n]))
                cue.region_id = region_id
                cue.style_id = table.id_for(Style(text_align=_AN_HORIZONTAL[n]))
            if ext:
                cue.extensions["srt"] = ext
            pending.append((cue, body))
        for cue, body in pending:
            cue.lines = _parse_text([b for b in body if b.strip()], table)
            cues.append(cue)
        return SubtitleDocument(source_format=self.name, styles=table.styles, regions=regions, cues=cues)

    def write(self, doc: SubtitleDocument) -> WriteResult:
        dropped = DroppedFeatures("SRT")
        blocks: list[str] = []
        for cue in doc.cues:
            lines = [self._render_line(doc, cue, line, dropped) for line in cue.lines]
            lines = [ln for ln in lines if ln.strip()]
            if not lines:
                dropped.add("empty cues")
                continue
            an = self._an_tag(doc, cue, dropped)
            if an:
                lines[0] = f"{{\\an{an}}}{lines[0]}"
            timing = f"{_timestamp(cue.start_ms)} --> {_timestamp(cue.end_ms)}"
            coords = cue.extensions.get("srt", {}).get("coordinates")
            if coords:
                timing += f" {coords}"
            blocks.append(f"{len(blocks) + 1}\n{timing}\n" + "\n".join(lines))
        return WriteResult(text="\n\n".join(blocks) + "\n" if blocks else "", warnings=dropped.warnings())

    @staticmethod
    def _an_tag(doc: SubtitleDocument, cue: Cue, dropped: DroppedFeatures) -> int | None:
        if "an" in cue.extensions.get("srt", {}):
            return cue.extensions["srt"]["an"]
        region = doc.regions.get(cue.region_id) if cue.region_id else None
        align = doc.resolve_style(cue).text_align
        vertical = region.display_align if region and region.display_align else "after"
        if region and (region.origin or region.extent):
            dropped.add("region position")
        row = {"after": 0, "center": 3, "before": 6}[vertical]
        col = {"left": 1, "start": 1, "right": 3, "end": 3}.get(align or "", 2)
        n = row + col
        return None if n == 2 else n

    @staticmethod
    def _render_line(doc: SubtitleDocument, cue: Cue, line: Line, dropped: DroppedFeatures) -> str:
        out = []
        for seg in line.segments:
            style = doc.resolve_style(cue, seg)
            opening, closing = [], []
            if style.color and style.color[:7] != _DEFAULT_COLOR:
                opening.append(f'<font color="{style.color[:7]}">')
                closing.append("</font>")
            if style.color and len(style.color) == 9:
                dropped.add("colour transparency")
            for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
                if getattr(style, flag):
                    opening.append(f"<{tag}>")
                    closing.append(f"</{tag}>")
            if style.background_color:
                dropped.add("background colour")
            if style.font_family:
                dropped.add("font family")
            if style.font_size:
                dropped.add("font size")
            if seg.voice:
                dropped.add("speaker names")
            out.append("".join(opening) + seg.text + "".join(reversed(closing)))
        return "".join(out)
