from pathlib import Path

import pytest

from subtitle_translate import formats
from subtitle_translate.formats import FormatError
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

EXAMPLES = Path(__file__).parent.parent / "examples"
ttml = formats.get("ttml")

HEAD = (
    '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" '
    'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xml:lang="en"{root}>'
    "<head><styling>"
    '<style xml:id="base" tts:color="white" tts:textAlign="center"/>'
    '<style xml:id="yellow" tts:color="rgba(255,255,0,200)" style="base"/>'
    '<style xml:id="b" tts:fontWeight="bold" tts:fontStyle="italic" tts:textDecoration="underline"/>'
    "</styling><layout>"
    '<region xml:id="top" tts:origin="10% 5%" tts:extent="80% 20%" tts:displayAlign="before"/>'
    '<region xml:id="px" tts:origin="10px 5px" tts:extent="80px 20px"/>'
    "</layout></head><body>{body}</body></tt>"
)


def doc_from(body, root=""):
    return ttml.read(HEAD.format(root=root, body=body))


@pytest.fixture
def sample_text():
    return (EXAMPLES / "sample.de.vtt").read_text(encoding="utf-8")


def test_sniff(sample_text):
    assert ttml.sniff(sample_text)
    assert ttml.sniff(HEAD.format(root="", body=""))
    assert not ttml.sniff("WEBVTT\n")
    assert not ttml.sniff('<?xml version="1.0"?><html/>')


def test_detected_despite_vtt_extension(sample_text):
    assert formats.detect(sample_text, "sample.de.vtt").name == "ttml"


def test_reads_sample(sample_text):
    doc = ttml.read(sample_text)
    assert doc.source_format == "ttml"
    assert doc.language == "de"
    assert len(doc.cues) == 13
    assert doc.regions["R1"] == Region(origin=(10, 10), extent=(80, 80), display_align="after")
    c6 = doc.cues[5]
    assert (c6.id, c6.start_ms, c6.end_ms) == ("C6", 18200, 21800)
    assert c6.text == "Du bist spät dran.\nDer Zug ist schon weg."
    assert c6.region_id == "R1" and c6.style_id == "S1 S2"


def test_sample_styles_and_two_speakers(sample_text):
    doc = ttml.read(sample_text)
    s4 = doc.styles["S4"]
    assert (s4.color, s4.background_color) == ("#00ffff", "#000000c2")
    cue_style = doc.resolve_style(doc.cues[0])
    assert (cue_style.font_size, cue_style.text_align) == ("160%", "center")
    c8 = doc.cues[7]
    assert len(c8.lines) == 1
    assert [(s.text, s.style_id) for s in c8.segments()] == [
        ("Und jetzt?", "S4"),
        (" Wir nehmen den nächsten.", "S5"),
    ]


def test_style_inheritance_rgba_and_flags():
    doc = doc_from('<div><p begin="0s" end="1s"><span style="yellow b">x</span></p></div>')
    cue = doc.cues[0]
    style = doc.resolve_style(cue, cue.segments()[0])
    assert style.color == "#ffff00c8"
    assert style.text_align == "center"
    assert (style.bold, style.italic, style.underline) == (True, True, True)


def test_nested_spans_text_in_p_and_whitespace():
    doc = doc_from(
        '<div><p begin="0s" end="1s">Hello\n   <span style="b">big   <span style="yellow">world</span></span>\n'
        "<br/>\n  second line  </p></div>"
    )
    cue = doc.cues[0]
    assert [(s.text, s.style_id) for s in cue.segments()] == [
        ("Hello ", None),
        ("big ", "b"),
        ("world", "b yellow"),
        (" second line ", None),
    ]
    assert len(cue.lines) == 2


@pytest.mark.parametrize(
    ("begin", "ms"),
    [
        ("0:00:02.800", 2800),
        ("12:00:24.560", 43_224_560),
        ("00:00:01:15", 1500),  # frames at the declared 30 fps
        ("2.5s", 2500),
        ("1500ms", 1500),
        ("1m", 60_000),
        ("0.5h", 1_800_000),
        ("45f", 1500),
        ("20000000t", 2000),
    ],
)
def test_time_expressions(begin, ms):
    doc = doc_from(
        f'<div><p begin="{begin}" end="99h">x</p></div>',
        root=' ttp:frameRate="30" ttp:tickRate="10000000"',
    )
    assert doc.cues[0].start_ms == ms


def test_regions_inherit_from_div_and_ignore_pixels():
    doc = doc_from('<div region="top"><p begin="0s" end="1s">a</p><p region="px" begin="1s" end="2s">b</p></div>')
    assert [c.region_id for c in doc.cues] == ["top", "px"]
    assert doc.regions["top"].display_align == "before"
    assert doc.regions["px"].origin is None


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("<tt", "invalid XML"),
        ('<root xmlns="http://www.w3.org/ns/ttml"/>', "root element is not <tt>"),
        (HEAD.format(root="", body='<div><p end="1s">x</p></div>'), "has no begin/end"),
        (HEAD.format(root="", body='<div><p begin="soon" end="1s">x</p></div>'), "unsupported time expression"),
        (HEAD.format(root="", body='<div><p style="nope" begin="0s" end="1s">x</p></div>'), "unknown style"),
        (HEAD.format(root="", body='<div><p region="nope" begin="0s" end="1s">x</p></div>'), "unknown region"),
        (HEAD.format(root="", body='<div><p begin="2s" end="1s">x</p></div>'), "ends"),
    ],
)
def test_malformed(text, match):
    with pytest.raises(FormatError, match=match):
        ttml.read(text)


def test_round_trip_sample_is_identical(sample_text):
    result = ttml.write(ttml.read(sample_text))
    assert result.warnings == []
    assert result.text == sample_text


def test_translated_text_only_touches_changed_paragraphs(sample_text):
    doc = ttml.read(sample_text)
    doc.language = "en"
    c8 = doc.cues[7].lines[0].segments
    c8[0].text, c8[1].text = "And now?", " We take the next one & coffee."
    out = ttml.write(doc).text
    assert 'xml:lang="en">' in out and 'xml:lang="de"' not in out
    assert (
        '<tt:p xml:id="C8" region="R1" style="S2" begin="0:00:25.800" end="0:00:28.600">\n'
        '<tt:span style="S4">And now?</tt:span>\n'
        '<tt:span style="S5"> We take the next one &amp; coffee.</tt:span>\n'
        "</tt:p>"
    ) in out
    # Everything outside C8 and xml:lang is untouched.
    before, after = sample_text.split('<tt:p xml:id="C8"')[0], out.split('<tt:p xml:id="C8"')[0]
    assert after == before.replace('xml:lang="de"', 'xml:lang="en"')
    reread = ttml.read(out)
    assert reread.cues[7].text == "And now? We take the next one & coffee."
    assert reread.cues[5].lines == doc.cues[5].lines


def test_multi_line_cue_rewritten_with_br_and_timing(sample_text):
    doc = ttml.read(sample_text)
    cue = doc.cues[5]
    cue.lines[0].segments[0].text = "You're late."
    cue.end_ms = 22_000
    out = ttml.write(doc).text
    assert (
        'begin="0:00:18.200" end="0:00:22.000">\n<tt:span style="S4">You\'re late.</tt:span>\n<tt:br/>\n'
        '<tt:span style="S4">Der Zug ist schon weg.</tt:span>\n</tt:p>'
    ) in out


def test_regenerates_when_cue_count_changes(sample_text):
    doc = ttml.read(sample_text)
    del doc.cues[0]
    out = ttml.write(doc).text
    assert out.count("<tt:p ") == 12
    assert ttml.read(out).cues[0].text == "Gelb für Anna Berger."


def test_generate_from_foreign_document():
    doc = SubtitleDocument(
        source_format="srt",
        language="en",
        styles={"y": Style(color="#ffff00", italic=True), "left": Style(text_align="left")},
        regions={"an8": Region(display_align="before")},
        cues=[
            Cue(id="1", start_ms=1000, end_ms=2500, lines=[
                Line(segments=[Segment(text="Plain <&> "), Segment(text="yellow", style_id="y")]),
                Line(segments=[Segment(text="second", voice="Anna")]),
            ]),
            Cue(id="2", start_ms=43_224_560, end_ms=43_225_160, region_id="an8", style_id="left",
                lines=[Line(segments=[Segment(text="top left")])]),
        ],
    )
    result = ttml.write(doc)
    assert result.warnings == ["EBU-TT-D cannot store speaker names; dropped on 1 cue"]
    back = ttml.read(result.text)
    assert back.language == "en"
    assert [(c.start_ms, c.end_ms, c.text) for c in back.cues] == [
        (1000, 2500, "Plain <&> yellow\nsecond"),
        (43_224_560, 43_225_160, "top left"),
    ]
    first = back.cues[0]
    yellow = back.resolve_style(first, first.lines[0].segments[1])
    assert (yellow.color, yellow.italic) == ("#ffff00", True)
    assert back.resolve_style(first).text_align == "center"
    second = back.cues[1]
    assert back.regions[second.region_id].display_align == "before"
    assert back.resolve_style(second).text_align == "left"
    assert 'begin="12:00:24.560"' in result.text
