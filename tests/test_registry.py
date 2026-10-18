import pytest

from subtitle_translate import formats
from subtitle_translate.formats import DroppedFeatures, FormatError, SubtitleFormat, WriteResult
from subtitle_translate.model import SubtitleDocument


def _make_format(name, exts, marker):
    class _Fmt(SubtitleFormat):
        def sniff(self, text):
            return marker in text

        def read(self, text):
            return SubtitleDocument(source_format=name)

        def write(self, doc):
            return WriteResult(text="")

    _Fmt.name = name
    _Fmt.extensions = exts
    return _Fmt


@pytest.fixture
def fake_formats():
    names = ["fake-a", "fake-b"]
    formats.register(_make_format("fake-a", (".fa", ".shared"), "AAA"))
    formats.register(_make_format("fake-b", (".fb", ".shared"), "BBB"))
    yield
    for n in names:
        formats.unregister(n)


def test_get_and_available(fake_formats):
    assert {"fake-a", "fake-b"} <= set(formats.available())
    assert formats.get("fake-a").name == "fake-a"


def test_get_unknown_lists_available(fake_formats):
    with pytest.raises(FormatError, match="unknown format 'nope'.*fake-a"):
        formats.get("nope")


def test_duplicate_registration_rejected(fake_formats):
    with pytest.raises(ValueError, match="already registered"):
        formats.register(_make_format("fake-a", (".x",), "X"))


def test_detect_uses_content_not_extension(fake_formats):
    # File named .fb but containing format A content is detected as A.
    assert formats.detect("AAA", "movie.fb").name == "fake-a"


def test_detect_extension_breaks_ties(fake_formats):
    assert formats.detect("AAA BBB", "movie.fb").name == "fake-b"


def test_detect_ambiguous(fake_formats):
    with pytest.raises(FormatError, match="ambiguous.*fake-a, fake-b"):
        formats.detect("AAA BBB", "movie.shared")


def test_detect_unknown(fake_formats):
    with pytest.raises(FormatError, match="could not detect subtitle format of x.txt"):
        formats.detect("nothing", "x.txt")


def test_dropped_features_one_warning_per_kind():
    dropped = DroppedFeatures("SRT")
    cues = [object() for _ in range(37)]
    for cue in cues:
        dropped.add("background colour", cue)
        dropped.add("background colour", cue)  # same cue twice counts once
    dropped.add("region", cues[0])
    assert dropped.warnings() == [
        "SRT cannot store background colour; dropped on 37 cues",
        "SRT cannot store region; dropped on 1 cue",
    ]
