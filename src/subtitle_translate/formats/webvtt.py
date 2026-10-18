"""WebVTT (.vtt) reader and writer.

Cue payload tags (``<c.cls>``, ``<i>``, ``<b>``, ``<u>``, ``<v Name>``) become
styled segments; colours come from ``::cue(.cls)`` rules in STYLE blocks and
the WebVTT default colour classes. For VTT-to-VTT round trips the original
tag chain of every style, header, STYLE/REGION/NOTE blocks and cue settings are
kept in ``extensions["vtt"]`` and written back unchanged.
"""

from __future__ import annotations

import html
import re

from subtitle_translate.colors import color_name, normalize_color
from subtitle_translate.formats.base import (
    DroppedFeatures,
    FormatError,
    StyleTable,
    SubtitleFormat,
    WriteResult,
)
from subtitle_translate.formats.registry import register
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

_TIME = r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})"
_TIMING = re.compile(rf"^\s*{_TIME}[ \t]+-->[ \t]+{_TIME}(.*)$")
# <name.cls1.cls2 annotation> or </name>; timestamp tags start with a digit.
_TAG = re.compile(r"<(/)?([^\s./>]+)((?:\.[^\s.>]+)*)(?:[ \t]+([^>]*))?>")
_CSS_RULE = re.compile(r"::cue\(\s*\.([A-Za-z0-9_-]+)\s*\)\s*\{([^}]*)\}")
_LANGUAGE_HEADER = re.compile(r"^Language:\s*(\S+)\s*$", re.IGNORECASE)

# Colour classes every WebVTT renderer must support.
_DEFAULT_CLASSES: dict[str, Style] = {
    name: Style(color=hex_value)
    for name, hex_value in {
        "white": "#ffffff", "lime": "#00ff00", "cyan": "#00ffff", "red": "#ff0000",
        "yellow": "#ffff00", "magenta": "#ff00ff", "blue": "#0000ff", "black": "#000000",
    }.items()
} | {
    f"bg_{name}": Style(background_color=hex_value)
    for name, hex_value in {
        "white": "#ffffff", "lime": "#00ff00", "cyan": "#00ffff", "red": "#ff0000",
        "yellow": "#ffff00", "magenta": "#ff00ff", "blue": "#0000ff", "black": "#000000",
    }.items()
}
_FLAG_TAGS = {"i": "italic", "b": "bold", "u": "underline"}
_STYLED_TAGS = {"c", "v", *_FLAG_TAGS}


def _ms(h: str | None, m: str, s: str, frac: str) -> int:
    return ((int(h or 0) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac)


def _timestamp(ms: int) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{rest:03d}"


def _css_style(declarations: str) -> Style:
    fields: dict = {}
    for decl in declarations.split(";"):
        if ":" not in decl:
            continue
        prop, value = (p.strip() for p in decl.split(":", 1))
        prop, low = prop.lower(), value.lower()
        try:
            if prop == "color":
                fields["color"] = normalize_color(value)
            elif prop in ("background-color", "background"):
                fields["background_color"] = normalize_color(value)
        except ValueError:
            pass
        if prop == "font-weight":
            fields["bold"] = low in ("bold", "bolder") or (low.isdigit() and int(low) >= 600)
        elif prop == "font-style":
            fields["italic"] = low in ("italic", "oblique")
        elif prop == "text-decoration":
            fields["underline"] = "underline" in low
        elif prop == "font-family":
            fields["font_family"] = value
        elif prop == "font-size":
            fields["font_size"] = value
    return Style(**fields)


def _css_declarations(style: Style) -> str:
    parts = []
    if style.color:
        parts.append(f"color: {style.color}")
    if style.background_color:
        parts.append(f"background-color: {style.background_color}")
    if style.font_family:
        parts.append(f"font-family: {style.font_family}")
    if style.font_size:
        parts.append(f"font-size: {style.font_size}")
    return "; ".join(parts)


def _parse_settings(settings: str) -> dict[str, str]:
    result = {}
    for item in settings.split():
        if ":" in item:
            key, value = item.split(":", 1)
            result[key] = value
    return result


def _percent(value: str) -> float | None:
    try:
        return float(value.rstrip("%")) if value.endswith("%") else None
    except ValueError:
        return None


Chain = tuple[tuple[str, tuple[str, ...]], ...]


@register
class WebVttFormat(SubtitleFormat):
    name = "vtt"
    extensions = (".vtt",)

    def sniff(self, text: str) -> bool:
        return re.match(r"WEBVTT(?:[ \t]|\r?\n|$)", text.lstrip("﻿")) is not None

    # ------------------------------------------------------------------ read

    def read(self, text: str) -> SubtitleDocument:
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        if not self.sniff(text):
            raise FormatError("WebVTT: file must start with 'WEBVTT'")
        blocks = re.split(r"\n[ \t]*\n", text.strip("\n"))
        header_lines = blocks[0].split("\n")
        language = None
        for line in header_lines[1:]:
            if m := _LANGUAGE_HEADER.match(line):
                language = m.group(1)

        class_styles = dict(_DEFAULT_CLASSES)
        vtt_regions: dict[str, Region] = {}
        prelude: list[str] = []  # STYLE / REGION / NOTE blocks before the first cue
        notes: list[str] = []
        raw_cues: list[tuple[str | None, re.Match, list[str], list[str]]] = []
        for block in blocks[1:]:
            first = block.split("\n", 1)[0]
            if first.startswith("NOTE") and (len(first) == 4 or first[4] in " \t"):
                (notes if raw_cues else prelude).append(block)
                continue
            if not raw_cues and first.strip() == "STYLE":
                prelude.append(block)
                for name, decls in _CSS_RULE.findall(block):
                    class_styles[name] = _css_style(decls)
                continue
            if not raw_cues and first.strip() == "REGION":
                prelude.append(block)
                self._read_region(block, vtt_regions)
                continue
            lines = block.split("\n")
            cue_id = None
            if "-->" not in lines[0]:
                cue_id, lines = lines[0], lines[1:]
            timing = _TIMING.match(lines[0]) if lines else None
            if not timing:
                raise FormatError(f"WebVTT: invalid cue timing in block starting {first!r}")
            raw_cues.append((cue_id, timing, lines[1:], notes))
            notes = []

        table = StyleTable()
        chains: dict[str, list] = {}
        regions: dict[str, Region] = dict(vtt_regions)
        cues = []
        for cue_id, timing, payload, notes_before in raw_cues:
            g = timing.groups()
            settings = g[8].strip()
            try:
                cue = Cue(id=cue_id, start_ms=_ms(*g[0:4]), end_ms=_ms(*g[4:8]))
            except ValueError as e:
                raise FormatError(f"WebVTT: invalid timing for cue {cue_id!r}: {e}") from None
            ext: dict = {}
            if settings:
                ext["settings"] = settings
            if notes_before:
                ext["notes_before"] = notes_before
            cue.extensions["vtt"] = ext
            self._apply_settings(cue, _parse_settings(settings), regions, table)
            cue.lines = self._parse_payload("\n".join(payload), class_styles, table, chains)
            cues.append(cue)

        return SubtitleDocument(
            source_format=self.name,
            language=language,
            styles=table.styles,
            regions=regions,
            cues=cues,
            extensions={"vtt": {"header": header_lines, "prelude": prelude, "chains": chains,
                                "trailing_notes": notes}},
        )

    @staticmethod
    def _read_region(block: str, regions: dict[str, Region]) -> None:
        attrs = _parse_settings(block.replace("\n", " "))
        if "id" not in attrs:
            return
        width = _percent(attrs.get("width", "100%")) or 100.0
        lines = int(attrs["lines"]) if attrs.get("lines", "").isdigit() else 3
        height = min(100.0, lines * 5.33)  # WebVTT: one line is 5.33vh by default
        ax, ay = (_percent(v) or 0.0 for v in attrs.get("regionanchor", "0%,100%").split(",", 1))
        vx, vy = (_percent(v) or 0.0 for v in attrs.get("viewportanchor", "0%,100%").split(",", 1))
        origin = (vx - width * ax / 100, vy - height * ay / 100)
        regions[attrs["id"]] = Region(origin=origin, extent=(width, height), display_align="after")

    @staticmethod
    def _apply_settings(cue: Cue, settings: dict[str, str], regions: dict[str, Region],
                        table: StyleTable) -> None:
        align = settings.get("align")
        if align in ("left", "right", "start", "end", "center"):
            cue.style_id = table.id_for(Style(text_align=align))
        if settings.get("region") in regions:
            cue.region_id = settings["region"]
            return
        line = settings.get("line", "").split(",")[0]
        if not line:
            return
        pct = _percent(line)
        if pct is not None:
            anchor = "before" if pct < 33 else "center" if pct < 66 else "after"
        else:
            try:
                anchor = "before" if int(line) >= 0 else "after"
            except ValueError:
                return
        region_id = f"vtt-{anchor}"
        regions.setdefault(region_id, Region(display_align=anchor))
        cue.region_id = region_id

    @staticmethod
    def _parse_payload(payload: str, class_styles: dict[str, Style], table: StyleTable,
                       chains: dict[str, list]) -> list[Line]:
        stack: list[tuple[str, tuple[str, ...], str | None]] = []
        lines = [Line()]
        pos = 0

        def emit(text: str) -> None:
            style = Style()
            voice = None
            chain = []
            for tag, classes, annotation in stack:
                if tag not in _STYLED_TAGS:
                    continue
                chain.append([tag, list(classes)])
                for cls in classes:
                    style = style.merged(class_styles.get(cls))
                if tag in _FLAG_TAGS:
                    style = style.merged(Style(**{_FLAG_TAGS[tag]: True}))
                if tag == "v":
                    voice = annotation
            style_id = table.id_for(style, key=repr(chain)) if chain else None
            if style_id:
                chains[style_id] = chain
            for i, part in enumerate(html.unescape(text).split("\n")):
                if i:
                    lines.append(Line())
                if part:
                    lines[-1].segments.append(Segment(text=part, style_id=style_id, voice=voice))

        for m in _TAG.finditer(payload):
            emit(payload[pos:m.start()])
            pos = m.end()
            closing, tag = m.group(1) == "/", m.group(2).lower()
            if tag[0].isdigit():
                continue  # karaoke timestamp
            if closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i:]
                        break
            else:
                classes = tuple(c for c in (m.group(3) or "").split(".") if c)
                stack.append((tag, classes, (m.group(4) or "").strip() or None))
        emit(payload[pos:])
        return lines

    # ----------------------------------------------------------------- write

    def write(self, doc: SubtitleDocument) -> WriteResult:
        dropped = DroppedFeatures("WebVTT")
        own = doc.extensions.get("vtt", {}) if doc.source_format == self.name else {}
        chains: dict[str, Chain] = {k: tuple((t, tuple(c)) for t, c in v)
                                    for k, v in own.get("chains", {}).items()}
        generated: dict[str, Style] = {}  # class name -> style, for a new STYLE block

        header = list(own.get("header", ["WEBVTT"]))
        if doc.language:
            header = [f"Language: {doc.language}" if _LANGUAGE_HEADER.match(h) else h for h in header]
        out = ["\n".join(header)]
        out.extend(own.get("prelude", []))

        cue_blocks = []
        for cue in doc.cues:
            ext = cue.extensions.get("vtt", {}) if own else {}
            cue_blocks.extend(ext.get("notes_before", []))
            settings = ext["settings"] if "settings" in ext else self._settings_for(doc, cue, dropped)
            timing = f"{_timestamp(cue.start_ms)} --> {_timestamp(cue.end_ms)}"
            if settings:
                timing += f" {settings}"
            payload = self._render_payload(doc, cue, chains, generated, dropped)
            cue_blocks.append("\n".join(filter(None, [cue.id, timing, payload])))
        cue_blocks.extend(own.get("trailing_notes", []))

        if generated:
            rules = "\n".join(f"::cue(.{name}) {{ {_css_declarations(style)}; }}"
                              for name, style in generated.items())
            out.append(f"STYLE\n{rules}")
        out.extend(cue_blocks)
        return WriteResult(text="\n\n".join(out) + "\n", warnings=dropped.warnings())

    @staticmethod
    def _settings_for(doc: SubtitleDocument, cue: Cue, dropped: DroppedFeatures) -> str:
        parts = []
        region = doc.regions.get(cue.region_id) if cue.region_id else None
        if region:
            if region.display_align == "before":
                parts.append(f"line:{region.origin[1] if region.origin else 0:g}%")
            elif region.display_align == "center":
                parts.append("line:50%,center")
            if region.origin or region.extent:
                dropped.add("region position", cue)
        align = doc.resolve_style(cue).text_align
        if align and align != "center":
            parts.append(f"align:{align}")
        return " ".join(parts)

    def _render_payload(self, doc: SubtitleDocument, cue: Cue, chains: dict[str, Chain],
                        generated: dict[str, Style], dropped: DroppedFeatures) -> str:
        out: list[str] = []
        open_stack: list[tuple[str, tuple[str, ...]]] = []
        open_voice: str | None = None

        def close_to(depth: int) -> None:
            while len(open_stack) > depth:
                out.append(f"</{open_stack.pop()[0]}>")

        newline = False  # emitted after closing tags that end on the previous line
        for i, line in enumerate(cue.lines):
            newline = newline or i > 0
            for seg in line.segments:
                chain = chains.get(seg.style_id) if seg.style_id in chains else self._chain_for(
                    doc, cue, seg, generated, dropped)
                if seg.voice and not any(t == "v" for t, _ in chain):
                    chain = (("v", ()), *chain)
                common = 0
                while (common < len(open_stack) and common < len(chain)
                       and open_stack[common] == chain[common]
                       and (chain[common][0] != "v" or open_voice == seg.voice)):
                    common += 1
                close_to(common)
                if newline:
                    out.append("\n")
                    newline = False
                for tag, classes in chain[common:]:
                    cls = "".join(f".{c}" for c in classes)
                    if tag == "v":
                        out.append(f"<v{cls} {seg.voice}>" if seg.voice else f"<v{cls}>")
                        open_voice = seg.voice
                    else:
                        out.append(f"<{tag}{cls}>")
                    open_stack.append((tag, classes))
                out.append(html.escape(seg.text, quote=False))
        # A voice span may stay open at the end of a cue.
        keep = 1 if open_stack and open_stack[0][0] == "v" else 0
        close_to(keep)
        return "".join(out)

    @staticmethod
    def _chain_for(doc: SubtitleDocument, cue: Cue, seg: Segment, generated: dict[str, Style],
                   dropped: DroppedFeatures) -> Chain:
        style = doc.resolve_style(cue, seg)
        css = Style(color=style.color, background_color=style.background_color,
                    font_family=style.font_family, font_size=style.font_size)
        chain: list[tuple[str, tuple[str, ...]]] = []
        if not css.is_empty():
            name = next((n for n, s in generated.items() if s == css), None)
            if name is None:
                only_color = css.model_dump(exclude_none=True).keys() == {"color"}
                name = (color_name(css.color) if only_color and css.color else None) or f"s{len(generated) + 1}"
                if name in generated:
                    name = f"s{len(generated) + 1}"
                generated[name] = css
            chain.append(("c", (name,)))
        for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
            if getattr(style, flag):
                chain.append((tag, ()))
        return tuple(chain)
