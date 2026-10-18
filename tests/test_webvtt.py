from pathlib import Path

import pytest

from subtitle_translate import formats
from subtitle_translate.formats import FormatError
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

EXAMPLES = Path(__file__).parent.parent / "examples"
vtt = formats.get("vtt")


@pytest.fixture
def sample():
    return (EXAMPLES / "sample.en.vtt").read_text(encoding="utf-8")


def seg_styles(doc, cue):
    return [(s.text, doc.resolve_style(cue, s), s.voice) for s in cue.segments()]


def test_sniff():
    assert vtt.sniff("WEBVTT\n\n")
    assert vtt.sniff("﻿WEBVTT - title\n")
    assert vtt.sniff("WEBVTT")
    assert not vtt.sniff("WEBVTTX\n")
    assert not vtt.sniff("1\n00:00:01,000 --> 00:00:02,000\nhi\n")


def test_reads_sample(sample):
    doc = vtt.read(sample)
    assert doc.source_format == "vtt"
    assert doc.language == "en"
    assert len(doc.cues) == 6
    assert doc.cues[0].id == "intro"
    assert (doc.cues[0].start_ms, doc.cues[0].end_ms) == (1000, 3200)


def test_classes_and_css_become_styles(sample):
    doc = vtt.read(sample)
    (text, style, voice), = seg_styles(doc, doc.cues[0])
    assert text == "[rain against the window]"
    assert style.color == "#ffffff" and style.italic is True and voice is None


def test_voice_spans_lines_and_two_speakers(sample):
    doc = vtt.read(sample)
    cue = doc.cues[1]
    assert cue.text == "You're late.\nThe train has already left."
    assert all(v == "Anna" and s.color == "#ffff00" for _, s, v in seg_styles(doc, cue))
    two = seg_styles(doc, doc.cues[3])
    assert [(t, s.color, v) for t, s, v in two] == [
        ("So what now?", "#ffff00", "Anna"),
        (" ", None, None),
        ("We take the next one.", "#00ff00", "Tom"),
    ]


def test_entities_and_nested_tags(sample):
    doc = vtt.read(sample)
    cue = doc.cues[5]
    assert cue.text == "Fine & dandy <3 – but this time you pay."
    italic = [t for t, s, _ in seg_styles(doc, cue) if s.italic]
    assert italic == ["this time"]


def test_settings_map_to_regions_and_alignment(sample):
    doc = vtt.read(sample)
    top = doc.cues[4]
    assert top.region_id == "top"
    assert doc.regions["top"].origin == (10.0, 10.0)
    assert doc.regions["top"].extent[0] == 80.0
    assert doc.regions[doc.cues[1].region_id].display_align == "after"
    last = doc.cues[5]
    assert doc.regions[last.region_id].display_align == "before"
    assert doc.resolve_style(last).text_align == "left"


def test_default_colour_classes_and_karaoke_timestamps():
    doc = vtt.read("WEBVTT\n\n00:01.000 --> 00:02.000\n<c.yellow.bg_blue>Hi</c> <00:01.500>there\n")
    cue = doc.cues[0]
    assert cue.start_ms == 1000  # hours are optional
    style = doc.resolve_style(cue, cue.segments()[0])
    assert (style.color, style.background_color) == ("#ffff00", "#0000ff")
    assert cue.text == "Hi there"


def test_round_trip_sample_is_identical(sample):
    result = vtt.write(vtt.read(sample))
    assert result.warnings == []
    assert result.text == sample


def test_round_trip_after_text_change_keeps_markup(sample):
    doc = vtt.read(sample)
    doc.language = "de"
    two = doc.cues[3].lines[0].segments
    two[0].text, two[2].text = "Und jetzt?", "Wir nehmen den nächsten."
    out = vtt.write(doc).text
    assert "Language: de" in out
    assert "<v.anna Anna>Und jetzt?</v> <v.tom Tom>Wir nehmen den nächsten.\n" in out


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("hello\n", "must start with 'WEBVTT'"),
        ("WEBVTT\n\nid\nnot a timing\ntext\n", "invalid cue timing"),
        ("WEBVTT\n\n00:00:05.000 --> 00:00:01.000\nback\n", "invalid timing"),
    ],
)
def test_malformed(text, match):
    with pytest.raises(FormatError, match=match):
        vtt.read(text)


def test_write_from_foreign_document_generates_style_block():
    doc = SubtitleDocument(
        source_format="ttml",
        language="de",
        styles={
            "S3": Style(color="#ffffff", background_color="#000000c2"),
            "S4": Style(color="#00ffff"),
            "S5": Style(color="#00ffff", italic=True),
        },
        regions={"R1": Region(origin=(10, 10), extent=(80, 80), display_align="after"),
                 "top": Region(origin=(10, 5), display_align="before")},
        cues=[
            Cue(id="C1", start_ms=0, end_ms=1000, region_id="R1",
                lines=[Line(segments=[Segment(text="A & B", style_id="S3")]),
                       Line(segments=[Segment(text="Gelb", style_id="S4"), Segment(text=" kursiv", style_id="S5")])]),
            Cue(id="C2", start_ms=1000, end_ms=2000, region_id="top",
                lines=[Line(segments=[Segment(text="oben", style_id="S4", voice="Anna")])]),
        ],
    )
    result = vtt.write(doc)
    assert result.text == (
        "WEBVTT\nLanguage: de\n\n"
        "STYLE\n"
        "::cue(.s1) { color: #ffffff; background-color: #000000c2; }\n"
        "::cue(.cyan) { color: #00ffff; }\n\n"
        "C1\n00:00:00.000 --> 00:00:01.000\n"
        "<c.s1>A &amp; B</c>\n<c.cyan>Gelb<i> kursiv</i></c>\n\n"
        "C2\n00:00:01.000 --> 00:00:02.000 line:5%\n"
        "<v Anna><c.cyan>oben</c>\n"
    )
    assert result.warnings == ["WebVTT cannot store region position; dropped on 2 cues"]


def test_named_entities_are_decoded_and_markup_chars_escaped():
    doc = vtt.read("WEBVTT\n\n00:00.000 --> 00:01.000\nA&nbsp;&ndash;&lrm;B &amp; &lt;i&gt;\n")
    assert doc.cues[0].text == "A –‎B & <i>"
    assert vtt.write(doc).text.endswith("A –‎B &amp; &lt;i&gt;\n")


def test_foreign_font_values_are_dropped_not_copied_into_css():
    doc = SubtitleDocument(
        source_format="ttml",
        styles={"S1": Style(color="#ffff00", font_family="proportionalSansSerif", font_size="160%")},
        cues=[Cue(start_ms=0, end_ms=1000, lines=[Line(segments=[Segment(text="Hi", style_id="S1")])])],
    )
    result = vtt.write(doc)
    assert "font" not in result.text
    assert "::cue(.yellow) { color: #ffff00; }" in result.text
    assert result.warnings == [
        "WebVTT cannot store font family; dropped on 1 cue",
        "WebVTT cannot store font size; dropped on 1 cue",
    ]


@pytest.mark.parametrize(("language", "header"), [("en", "WEBVTT\nLanguage: en\n\n"),
                                                  (None, "WEBVTT\n\n"), ("und", "WEBVTT\n\n")])
def test_language_header_for_foreign_document(language, header):
    doc = SubtitleDocument(source_format="srt", language=language,
                           cues=[Cue(start_ms=0, end_ms=1000, lines=[Line(segments=[Segment(text="Hi")])])])
    assert vtt.write(doc).text.startswith(header)
