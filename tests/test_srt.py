from pathlib import Path

import pytest

from subtitle_translate import formats
from subtitle_translate.formats import FormatError
from subtitle_translate.model import Cue, Line, Region, Segment, Style, SubtitleDocument

EXAMPLES = Path(__file__).parent.parent / "examples"
srt = formats.get("srt")


def styles_of(doc, cue):
    return [(s.text, doc.resolve_style(cue, s)) for s in cue.segments()]


def test_reads_sample_file():
    doc = srt.read((EXAMPLES / "sample.en.srt").read_text(encoding="utf-8"))
    assert doc.source_format == "srt"
    assert len(doc.cues) == 7
    first = doc.cues[0]
    assert (first.id, first.start_ms, first.end_ms) == ("1", 1000, 3500)
    assert doc.cues[1].text == "Not really. The neighbours were\nplaying music until 2 a.m."
    door = doc.cues[2]
    assert door.text == "[door slams]"
    assert doc.resolve_style(door, door.segments()[0]).italic is True
    assert doc.cues[6].start_ms == 62100


def test_sniff():
    assert srt.sniff("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    assert srt.sniff("﻿\n00:00:01.000 --> 00:00:02.000\nHi\n")  # no index, dot separator
    assert not srt.sniff("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n")
    assert not srt.sniff('<?xml version="1.0"?><tt/>')
    assert not srt.sniff("")


def test_inline_markup_becomes_styled_segments():
    doc = srt.read(
        '1\n00:00:00,000 --> 00:00:01,000\n<font color="yellow">Gelb <b>fett</b></font> normal\n'
    )
    cue = doc.cues[0]
    parts = styles_of(doc, cue)
    assert [p[0] for p in parts] == ["Gelb ", "fett", " normal"]
    assert parts[0][1].color == "#ffff00" and not parts[0][1].bold
    assert parts[1][1].color == "#ffff00" and parts[1][1].bold
    assert parts[2][1].is_empty()


def test_tags_span_lines_and_unknown_tags_stay_literal():
    doc = srt.read("1\n00:00:00,000 --> 00:00:01,000\n<i>one\ntwo</i> <3 <s>x</s>\n")
    cue = doc.cues[0]
    assert cue.text == "one\ntwo <3 <s>x</s>"
    assert doc.resolve_style(cue, cue.lines[1].segments[0]).italic is True
    assert doc.resolve_style(cue, cue.lines[1].segments[1]).italic is None


def test_position_tag_maps_to_region_and_alignment():
    doc = srt.read("1\n00:00:00,000 --> 00:00:01,000\n{\\an7}Top left\n")
    cue = doc.cues[0]
    assert cue.text == "Top left"
    assert doc.regions[cue.region_id].display_align == "before"
    assert doc.resolve_style(cue).text_align == "left"


def test_crlf_ms_padding_and_blank_line_inside_text():
    text = "1\r\n00:00:01,5 --> 00:00:02,25\r\nfirst\r\n\r\nstill first\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nsecond\r\n"
    doc = srt.read(text)
    assert [c.text for c in doc.cues] == ["first\nstill first", "second"]
    assert (doc.cues[0].start_ms, doc.cues[0].end_ms) == (1500, 2250)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("hello\nworld\n", "expected a timing line"),
        ("1\n00:00:05,000 --> 00:00:01,000\nbackwards\n", "invalid timing"),
    ],
)
def test_malformed_input(text, match):
    with pytest.raises(FormatError, match=match):
        srt.read(text)


def test_round_trip_sample_is_identical():
    original = (EXAMPLES / "sample.en.srt").read_text(encoding="utf-8")
    result = srt.write(srt.read(original))
    assert result.warnings == []
    assert result.text == original


def test_round_trip_markup_and_coordinates():
    original = (
        "1\n00:00:00,000 --> 00:00:01,000 X1:10 X2:20 Y1:30 Y2:40\n"
        '{\\an8}<font color="#ffff00"><b>Hi</b></font> there\n<i>there</i>\n'
    )
    assert srt.write(srt.read(original)).text == original


def test_write_renumbers_and_formats_long_timestamps():
    doc = SubtitleDocument(
        cues=[
            Cue(id="C7", start_ms=43_224_560, end_ms=43_225_160, lines=[Line(segments=[Segment(text="late")])]),
        ]
    )
    assert srt.write(doc).text == "1\n12:00:24,560 --> 12:00:25,160\nlate\n"


def test_write_reports_dropped_features_and_skips_white():
    doc = SubtitleDocument(
        styles={
            "S3": Style(color="#ffffff", background_color="#000000c2"),
            "S4": Style(color="#00ffffc2", font_family="Verdana"),
        },
        regions={"R1": Region(origin=(10, 10), extent=(80, 80), display_align="after")},
        cues=[
            Cue(
                start_ms=0,
                end_ms=1000,
                region_id="R1",
                lines=[Line(segments=[Segment(text="Weiß ", style_id="S3"), Segment(text="Gelb", style_id="S4", voice="Anna")])],
            ),
            Cue(start_ms=1000, end_ms=2000, lines=[Line(segments=[Segment(text="  ")])]),
        ],
    )
    result = srt.write(doc)
    assert result.text == '1\n00:00:00,000 --> 00:00:01,000\nWeiß <font color="#00ffff">Gelb</font>\n'
    assert result.warnings == [
        "SRT cannot store background colour; dropped on 1 cue",
        "SRT cannot store colour transparency; dropped on 1 cue",
        "SRT cannot store empty cues; dropped on 1 cue",
        "SRT cannot store font family; dropped on 1 cue",
        "SRT cannot store region position; dropped on 1 cue",
        "SRT cannot store speaker names; dropped on 1 cue",
    ]


def test_write_derives_position_tag_from_region():
    doc = SubtitleDocument(
        styles={"r": Style(text_align="right")},
        regions={"top": Region(display_align="before")},
        cues=[Cue(start_ms=0, end_ms=1, region_id="top", style_id="r", lines=[Line(segments=[Segment(text="x")])])],
    )
    assert "{\\an9}x" in srt.write(doc).text
