"""Every example file through every writer: timing, text and colours survive."""

import re
from pathlib import Path

import pytest

from subtitle_translate import formats

EXAMPLES = Path(__file__).parent.parent / "examples"
SAMPLES = {
    "sample.en.srt": "srt",
    "sample.en.vtt": "vtt",
    "sample.de.vtt": "ttml",  # ARD-style TTML in a .vtt file
}
TARGETS = formats.available()


def load(name):
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    return formats.detect(text, name).read(text)


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def colours(doc):
    """Effective foreground colour (alpha dropped) of every segment, in order."""
    return [
        (doc.resolve_style(cue, seg).color or "#ffffff")[:7]
        for cue in doc.cues
        for seg in cue.segments()
        if seg.text.strip()
    ]


def test_all_formats_registered():
    assert TARGETS == ["srt", "ttml", "vtt"]


@pytest.mark.parametrize(("name", "fmt"), SAMPLES.items())
def test_examples_detected_by_content(name, fmt):
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    assert formats.detect(text, name).name == fmt


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("name", SAMPLES)
def test_conversion_keeps_timing_text_and_colours(name, target):
    source = load(name)
    result = formats.get(target).write(source)
    back = formats.get(target).read(result.text)

    assert [(c.start_ms, c.end_ms) for c in back.cues] == [(c.start_ms, c.end_ms) for c in source.cues]
    assert [norm(c.text) for c in back.cues] == [norm(c.text) for c in source.cues]
    assert colours(back) == colours(source)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("name", SAMPLES)
def test_line_structure_survives(name, target):
    source = load(name)
    back = formats.get(target).read(formats.get(target).write(source).text)
    assert [len(c.lines) for c in back.cues] == [len(c.lines) for c in source.cues]


@pytest.mark.parametrize(
    ("name", "target", "expected"),
    [
        ("sample.de.vtt", "srt", [
            "SRT cannot store background colour; dropped on 13 cues",
            "SRT cannot store font family; dropped on 13 cues",
            "SRT cannot store font size; dropped on 13 cues",
            "SRT cannot store region position; dropped on 13 cues",
        ]),
        ("sample.de.vtt", "vtt", ["WebVTT cannot store region position; dropped on 13 cues"]),
        ("sample.en.vtt", "srt", [
            "SRT cannot store region position; dropped on 1 cue",
            "SRT cannot store speaker names; dropped on 4 cues",
        ]),
        ("sample.en.vtt", "ttml", ["EBU-TT-D cannot store speaker names; dropped on 4 cues"]),
        ("sample.en.srt", "vtt", []),
        ("sample.en.srt", "ttml", []),
    ],
)
def test_expected_warnings(name, target, expected):
    assert formats.get(target).write(load(name)).warnings == expected


def test_language_carried_into_ttml():
    ttml = formats.get("ttml")
    assert ttml.read(ttml.write(load("sample.en.vtt")).text).language == "en"
    assert ttml.read(ttml.write(load("sample.en.srt")).text).language == "und"  # SRT has no language
