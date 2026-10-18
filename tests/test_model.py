import pytest
from pydantic import ValidationError

from subtitle_translate.colors import color_name, normalize_color
from subtitle_translate.model import Cue, Line, Segment, Style, SubtitleDocument


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#FFFFFF", "#ffffff"),
        ("ffffff", "#ffffff"),
        ("#0f0", "#00ff00"),
        ("#000000c2", "#000000c2"),
        ("Yellow", "#ffff00"),
        (" cyan ", "#00ffff"),
    ],
)
def test_normalize_color(raw, expected):
    assert normalize_color(raw) == expected


@pytest.mark.parametrize("raw", ["", "#12", "rgb(1,2,3)", "notacolour"])
def test_normalize_color_rejects(raw):
    with pytest.raises(ValueError):
        normalize_color(raw)


def test_color_name_ignores_alpha():
    assert color_name("#ffff00c2") == "yellow"
    assert color_name("#123456") is None


def test_style_normalises_colours_and_merges():
    base = Style(color="white", background_color="#000000C2", italic=True)
    assert base.color == "#ffffff"
    merged = base.merged(Style(color="#00ffff"))
    assert merged.color == "#00ffff"
    assert merged.background_color == "#000000c2"
    assert merged.italic is True
    assert Style().is_empty() and not base.is_empty()


def test_cue_text_and_segments():
    cue = Cue(
        id="C8",
        start_ms=25800,
        end_ms=28600,
        lines=[Line(segments=[Segment(text="Und jetzt?"), Segment(text=" Wir gehen.")])],
    )
    assert cue.text == "Und jetzt? Wir gehen."
    assert len(cue.segments()) == 2


def test_cue_rejects_negative_duration():
    with pytest.raises(ValidationError, match="ends"):
        Cue(start_ms=2000, end_ms=1000)


def test_document_checks_style_and_region_references():
    with pytest.raises(ValidationError, match="unknown style"):
        SubtitleDocument(
            cues=[Cue(start_ms=0, end_ms=1, lines=[Line(segments=[Segment(text="x", style_id="S9")])])]
        )
    with pytest.raises(ValidationError, match="unknown region"):
        SubtitleDocument(cues=[Cue(start_ms=0, end_ms=1, region_id="R1")])


def test_resolve_style_segment_overrides_cue():
    seg = Segment(text="Ja.", style_id="yellow")
    cue = Cue(start_ms=0, end_ms=1, style_id="base", lines=[Line(segments=[seg])])
    doc = SubtitleDocument(
        styles={"base": Style(color="white", text_align="center"), "yellow": Style(color="#00ffff")},
        cues=[cue],
    )
    style = doc.resolve_style(cue, seg)
    assert style.color == "#00ffff"
    assert style.text_align == "center"


def test_document_json_round_trip():
    doc = SubtitleDocument(
        source_format="srt",
        language="en",
        styles={"s": Style(bold=True)},
        cues=[Cue(id="1", start_ms=0, end_ms=500, lines=[Line(segments=[Segment(text="Hi", style_id="s")])])],
        extensions={"srt": {"raw": True}},
    )
    assert SubtitleDocument.model_validate_json(doc.model_dump_json()) == doc
