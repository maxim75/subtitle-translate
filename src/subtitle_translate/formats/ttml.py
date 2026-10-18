"""EBU-TT-D (TTML) reader and writer.

Reads generic TTML: styles (including multiple and nested style references),
regions in percent, ``<p>`` cues with ``<span>``/``<br/>`` content, and the
common time expressions. Style ids in the model are the TTML ``style``
attribute values, so a span with ``style="S3 S4"`` gets the id ``"S3 S4"``.

When the document came from TTML the writer edits the original file text in
place: only the content (and changed timing) of each ``<p>`` and the root
``xml:lang`` are replaced, so the head, comments and layout survive byte for
byte. Documents from other formats get a freshly generated EBU-TT-D file.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from subtitle_translate.colors import normalize_color
from subtitle_translate.formats.base import DroppedFeatures, FormatError, SubtitleFormat, WriteResult
from subtitle_translate.formats.registry import register
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

TT = "http://www.w3.org/ns/ttml"
TTS = "http://www.w3.org/ns/ttml#styling"
TTP = "http://www.w3.org/ns/ttml#parameter"
XML = "http://www.w3.org/XML/1998/namespace"

_CLOCK = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:(\.\d+)|:(\d+)(?:\.(\d+))?)?$")
_OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms|f|t)$")
_RGB = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)$")
_P_ELEMENT = re.compile(r"<(?P<prefix>[\w.-]+:)?p\b(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</(?:[\w.-]+:)?p\s*>)", re.S)
_ROOT_LANG = re.compile(r"""(<(?:[\w.-]+:)?tt\b[^>]*?\sxml:lang\s*=\s*)(["'])(.*?)\2""", re.S)


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _color(value: str) -> str | None:
    m = _RGB.match(value.strip())
    if m:
        r, g, b, a = m.groups()
        return f"#{int(r):02x}{int(g):02x}{int(b):02x}" + (f"{int(a):02x}" if a is not None else "")
    try:
        return normalize_color(value)
    except ValueError:
        return None


def _percent_pair(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    parts = value.split()
    if len(parts) != 2 or not all(p.endswith("%") for p in parts):
        return None
    try:
        return float(parts[0][:-1]), float(parts[1][:-1])
    except ValueError:
        return None


class _Clock:
    def __init__(self, root: ET.Element) -> None:
        self.frame_rate = float(root.get(_q(TTP, "frameRate"), "30"))
        self.tick_rate = float(root.get(_q(TTP, "tickRate"), "1"))

    def ms(self, value: str) -> int:
        value = value.strip()
        if m := _CLOCK.match(value):
            h, mi, s, frac, frames, sub = m.groups()
            total = (int(h) * 60 + int(mi)) * 60 + int(s) + (float(frac) if frac else 0.0)
            if frames:
                total += int(frames) / self.frame_rate
            return round(total * 1000)
        if m := _OFFSET.match(value):
            n, unit = float(m.group(1)), m.group(2)
            seconds = {"h": n * 3600, "m": n * 60, "s": n, "ms": n / 1000,
                       "f": n / self.frame_rate, "t": n / self.tick_rate}[unit]
            return round(seconds * 1000)
        raise FormatError(f"TTML: unsupported time expression {value!r}")


def _clock_text(ms: int, single_digit_hours: bool = False) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h:{'d' if single_digit_hours else '02d'}}:{m:02d}:{s:02d}.{rest:03d}"


@register
class TtmlFormat(SubtitleFormat):
    name = "ttml"
    extensions = (".ttml", ".xml", ".dfxp")

    def sniff(self, text: str) -> bool:
        head = text.lstrip("﻿")[:4096]
        return TT + '"' in head.replace("'", '"') and re.search(r"<([\w.-]+:)?tt[\s>]", head) is not None

    # ------------------------------------------------------------------ read

    def read(self, text: str) -> SubtitleDocument:
        text = text.lstrip("﻿")
        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as e:
            raise FormatError(f"TTML: invalid XML: {e}") from None
        if root.tag != _q(TT, "tt"):
            raise FormatError("TTML: root element is not <tt>")
        clock = _Clock(root)

        raw_styles: dict[str, tuple[Style, list[str]]] = {}
        for el in root.iter(_q(TT, "style")):
            sid = el.get(_q(XML, "id"))
            if sid:
                raw_styles[sid] = (self._style_attrs(el), (el.get("style") or "").split())
        styles: dict[str, Style] = {}

        def style_ref(ref: str) -> str | None:
            """Register a (possibly multi-id) style reference; return its id."""
            ids = ref.split()
            if not ids:
                return None
            if ref not in styles:
                merged = Style()
                for sid in ids:
                    merged = merged.merged(resolve(sid, set()))
                styles[ref] = merged
            return ref

        def resolve(sid: str, seen: set[str]) -> Style:
            if sid not in raw_styles:
                raise FormatError(f"TTML: unknown style {sid!r}")
            if sid in seen:
                raise FormatError(f"TTML: circular style reference via {sid!r}")
            own, parents = raw_styles[sid]
            base = Style()
            for parent in parents:
                base = base.merged(resolve(parent, seen | {sid}))
            return base.merged(own)

        for sid in raw_styles:
            style_ref(sid)

        regions: dict[str, Region] = {}
        for el in root.iter(_q(TT, "region")):
            rid = el.get(_q(XML, "id"))
            if rid:
                align = el.get(_q(TTS, "displayAlign"))
                regions[rid] = Region(
                    origin=_percent_pair(el.get(_q(TTS, "origin"))),
                    extent=_percent_pair(el.get(_q(TTS, "extent"))),
                    display_align=align if align in ("before", "center", "after") else None,
                )

        cues: list[Cue] = []
        body = root.find(_q(TT, "body"))
        if body is not None:
            self._walk(body, [], body.get("region"), clock, style_ref, regions, cues)
        return SubtitleDocument(
            source_format=self.name,
            language=root.get(_q(XML, "lang")) or None,
            styles=styles,
            regions=regions,
            cues=cues,
            extensions={"ttml": {"source": text, "single_digit_hours": self._single_digit_hours(text)}},
        )

    @staticmethod
    def _single_digit_hours(text: str) -> bool:
        m = re.search(r'\bbegin="(\d+):\d{2}:\d{2}', text)
        return bool(m) and len(m.group(1)) == 1

    @staticmethod
    def _style_attrs(el: ET.Element) -> Style:
        get = lambda name: el.get(_q(TTS, name))  # noqa: E731
        fields: dict = {}
        if (c := get("color")) and (c := _color(c)):
            fields["color"] = c
        if (c := get("backgroundColor")) and (c := _color(c)):
            fields["background_color"] = c
        if w := get("fontWeight"):
            fields["bold"] = w == "bold"
        if s := get("fontStyle"):
            fields["italic"] = s in ("italic", "oblique")
        if d := get("textDecoration"):
            fields["underline"] = "underline" in d and "noUnderline" not in d
        if f := get("fontFamily"):
            fields["font_family"] = f
        if f := get("fontSize"):
            fields["font_size"] = f
        if (a := get("textAlign")) in ("left", "center", "right", "start", "end"):
            fields["text_align"] = a
        return Style(**fields)

    def _walk(self, el, style_stack, region, clock, style_ref, regions, cues) -> None:
        for child in el:
            styles = style_stack + (child.get("style") or "").split()
            child_region = child.get("region") or region
            if child.tag == _q(TT, "div"):
                self._walk(child, styles, child_region, clock, style_ref, regions, cues)
            elif child.tag == _q(TT, "p"):
                begin, end = child.get("begin"), child.get("end")
                if begin is None or end is None:
                    raise FormatError(f"TTML: <p> {child.get(_q(XML, 'id'))!r} has no begin/end")
                if child_region is not None and child_region not in regions:
                    raise FormatError(f"TTML: unknown region {child_region!r}")
                try:
                    cue = Cue(
                        id=child.get(_q(XML, "id")),
                        start_ms=clock.ms(begin),
                        end_ms=clock.ms(end),
                        region_id=child_region,
                        style_id=style_ref(" ".join(styles)),
                    )
                except ValueError as e:
                    raise FormatError(f"TTML: {e}") from None
                lines = [Line()]
                self._inline(child, [], lines, style_ref)
                cue.lines = [ln for ln in lines if ln.segments] or [Line()]
                cues.append(cue)

    def _inline(self, el, span_styles, lines, style_ref) -> None:
        def add(text: str | None) -> None:
            if not text or (not text.strip() and "\n" in text):
                return  # layout whitespace between elements
            text = re.sub(r"\s+", " ", text)
            sid = style_ref(" ".join(span_styles)) if span_styles else None
            lines[-1].segments.append(Segment(text=text, style_id=sid))

        add(el.text)
        for child in el:
            if child.tag == _q(TT, "br"):
                lines.append(Line())
            elif child.tag == _q(TT, "span"):
                self._inline(child, span_styles + (child.get("style") or "").split(), lines, style_ref)
            add(child.tail)

    # ----------------------------------------------------------------- write

    def write(self, doc: SubtitleDocument) -> WriteResult:
        dropped = DroppedFeatures("EBU-TT-D")
        for cue in doc.cues:
            if any(s.voice for s in cue.segments()):
                dropped.add("speaker names")
        source = doc.extensions.get("ttml", {}).get("source") if doc.source_format == self.name else None
        text = self._patch_source(doc, source) if source else None
        if text is None:
            text = self._generate(doc)
        return WriteResult(text=text, warnings=dropped.warnings())

    @staticmethod
    def _content(prefix: str, cue: Cue, style_attr) -> str:
        lines = []
        for line in cue.lines:
            spans = []
            for seg in line.segments:
                style = style_attr(seg)
                attr = f" style={quoteattr(style)}" if style else ""
                spans.append(f"<{prefix}span{attr}>{escape(seg.text)}</{prefix}span>")
            lines.append("\n".join(spans))
        return "\n" + f"\n<{prefix}br/>\n".join(lines) + "\n"

    def _patch_source(self, doc: SubtitleDocument, source: str) -> str | None:
        matches = list(_P_ELEMENT.finditer(source))
        if len(matches) != len(doc.cues):
            return None  # cues were added or removed: regenerate instead
        original = self.read(source)
        hours1 = doc.extensions["ttml"].get("single_digit_hours", False)
        out, pos = [], 0
        for m, cue, before in zip(matches, doc.cues, original.cues):
            prefix = m.group("prefix") or ""
            attrs = m.group("attrs")
            if cue.start_ms != before.start_ms:
                attrs = re.sub(r'\bbegin="[^"]*"', f'begin="{_clock_text(cue.start_ms, hours1)}"', attrs)
            if cue.end_ms != before.end_ms:
                attrs = re.sub(r'\bend="[^"]*"', f'end="{_clock_text(cue.end_ms, hours1)}"', attrs)
            if cue.lines == before.lines and attrs == m.group("attrs"):
                content = source[m.start():m.end()]
            else:
                body = self._content(prefix, cue, lambda seg: seg.style_id)
                content = f"<{prefix}p{attrs}>{body}</{prefix}p>"
            out.append(source[pos:m.start()])
            out.append(content)
            pos = m.end()
        out.append(source[pos:])
        text = "".join(out)
        if doc.language and doc.language != original.language:
            text = _ROOT_LANG.sub(lambda r: f"{r.group(1)}{r.group(2)}{doc.language}{r.group(2)}", text, count=1)
        return text

    def _generate(self, doc: SubtitleDocument) -> str:
        style_ids: dict[str, str] = {}  # Style JSON -> generated id
        style_xml: list[str] = []

        def style_for(style: Style) -> str | None:
            if style.is_empty():
                return None
            key = style.model_dump_json()
            if key not in style_ids:
                style_ids[key] = f"S{len(style_ids) + 1}"
                style_xml.append(self._style_element(style_ids[key], style))
            return style_ids[key]

        base = style_for(Style(font_family="Verdana, Arial, Tiresias", font_size="160%"))
        region_ids: dict[str | None, str] = {}
        region_xml: list[str] = []

        def region_for(region_id: str | None) -> str:
            if region_id not in region_ids:
                region = doc.regions.get(region_id) if region_id else None
                rid = f"R{len(region_ids) + 1}"
                region_ids[region_id] = rid
                origin = region.origin if region and region.origin else (10.0, 10.0)
                extent = region.extent if region and region.extent else (80.0, 80.0)
                align = region.display_align if region and region.display_align else "after"
                region_xml.append(
                    f'<tt:region xml:id="{rid}" tts:origin="{origin[0]:g}% {origin[1]:g}%" '
                    f'tts:extent="{extent[0]:g}% {extent[1]:g}%" tts:displayAlign="{align}"/>'
                )
            return region_ids[region_id]

        paragraphs = []
        for i, cue in enumerate(doc.cues, start=1):
            cue_style = doc.resolve_style(cue)
            p_style = style_for(Style(text_align=cue_style.text_align or "center"))

            def span_style(seg: Segment, cue: Cue = cue) -> str | None:
                s = doc.resolve_style(cue, seg).model_copy(update={"text_align": None})
                return style_for(s)

            body = self._content("tt:", cue, span_style)
            paragraphs.append(
                f'<tt:p xml:id="C{i}" region="{region_for(cue.region_id)}" style="{p_style}" '
                f'begin="{_clock_text(cue.start_ms)}" end="{_clock_text(cue.end_ms)}">{body}</tt:p>'
            )
        if not region_ids:
            region_for(None)
        lang = doc.language or "und"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!-- Profile: EBU-TT-D-Basic-DE --><tt:tt xmlns:tt="http://www.w3.org/ns/ttml" '
            'xmlns:tts="http://www.w3.org/ns/ttml#styling"\n'
            'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"\n'
            'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"\n'
            'xmlns:ebuttm="urn:ebu:tt:metadata"\n'
            'ttp:timeBase="media"\n'
            'ttp:cellResolution="50 30"\n'
            f"xml:lang={quoteattr(lang)}>\n"
            "<tt:head>\n<tt:metadata>\n<ebuttm:documentMetadata>\n"
            "<ebuttm:documentEbuttVersion>v1.0</ebuttm:documentEbuttVersion>\n"
            "</ebuttm:documentMetadata>\n</tt:metadata>\n"
            "<tt:styling>\n" + "\n".join(style_xml) + "\n</tt:styling>\n"
            "<tt:layout>\n" + "\n".join(region_xml) + "\n</tt:layout>\n"
            "</tt:head>\n"
            f'<tt:body>\n<tt:div style="{base}">\n' + "\n".join(paragraphs) + "\n</tt:div>\n</tt:body>\n</tt:tt>\n"
        )

    @staticmethod
    def _style_element(sid: str, style: Style) -> str:
        attrs = []
        if style.font_size:
            attrs.append(f"tts:fontSize={quoteattr(style.font_size)}")
        if style.font_family:
            attrs.append(f"tts:fontFamily={quoteattr(style.font_family)}")
        if style.text_align:
            attrs.append(f'tts:textAlign="{style.text_align}"')
        if style.color:
            attrs.append(f'tts:color="{style.color}"')
        if style.background_color:
            attrs.append(f'tts:backgroundColor="{style.background_color}"')
        if style.bold:
            attrs.append('tts:fontWeight="bold"')
        if style.italic:
            attrs.append('tts:fontStyle="italic"')
        if style.underline:
            attrs.append('tts:textDecoration="underline"')
        return f'<tt:style xml:id="{sid}" ' + " ".join(attrs) + "/>"
