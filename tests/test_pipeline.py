from pathlib import Path

import pytest

from subtitle_translate import formats
from subtitle_translate.model import Cue, Line, Segment, Style, SubtitleDocument
from subtitle_translate.translate import (
    CueText,
    InvalidResponseError,
    TranslationError,
    TranslationResponse,
    translate_document,
)

EXAMPLES = Path(__file__).parent.parent / "examples"


def load(name):
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    return formats.detect(text, name).read(text)


class FakeTranslator:
    """Upper-cases text; ``fn(request)`` can override the answer per call."""

    def __init__(self, fn=None):
        self.requests = []
        self.fn = fn

    def translate(self, request):
        self.requests.append(request)
        if self.fn:
            result = self.fn(request, len(self.requests))
            if result is not None:
                return result
        return TranslationResponse(cues=[CueText(id=c.id, segments=[s.upper() for s in c.segments]) for c in request.cues])


def doc_with(*cues):
    return SubtitleDocument(styles={"a": Style(color="yellow"), "b": Style(color="lime")}, cues=[
        Cue(id=str(i), start_ms=i * 1000, end_ms=i * 1000 + 900, lines=[Line(segments=segs) for segs in lines])
        for i, lines in enumerate(cues, start=1)
    ])


def test_translates_sample_keeping_styles_and_language():
    source = load("sample.de.vtt")
    fake = FakeTranslator()
    result = translate_document(source, fake, "en")
    doc = result.document
    assert result.warnings == []
    assert doc.language == "en"
    assert source.language == "de" and source.cues[7].text == "Und jetzt? Wir nehmen den nächsten."  # input untouched
    c8 = doc.cues[7]
    assert [(s.text, s.style_id) for s in c8.segments()] == [("UND JETZT?", "S4"), (" WIR NEHMEN DEN NÄCHSTEN.", "S5")]
    assert [c.start_ms for c in doc.cues] == [c.start_ms for c in source.cues]
    request = fake.requests[0]
    assert request.source_language == "de" and request.target_language == "en"
    assert request.cues[7].segments == ["Und jetzt?", "Wir nehmen den nächsten."]


def test_soft_line_breaks_are_rewrapped():
    doc = load("sample.de.vtt")
    c6 = doc.cues[5]
    assert len(c6.lines) == 2
    fake = FakeTranslator()
    out = translate_document(doc, fake, "en").document.cues[5]
    # One segment for the whole two-line sentence, laid out again within 42 chars.
    assert fake.requests[0].cues[5].segments == ["Du bist spät dran. Der Zug ist schon weg."]
    assert out.text == "DU BIST SPÄT DRAN. DER ZUG IST SCHON WEG."
    narrow = translate_document(doc, FakeTranslator(), "en", max_line_length=25).document.cues[5]
    assert narrow.text == "DU BIST SPÄT DRAN.\nDER ZUG IST SCHON WEG."


def test_dialogue_dashes_keep_their_lines():
    doc = load("sample.en.srt")
    fake = FakeTranslator()
    out = translate_document(doc, fake, "de").document
    assert fake.requests[0].cues[3].segments == ["- Is that them again?", "- I'm afraid so."]
    assert out.cues[3].text == "- IS THAT THEM AGAIN?\n- I'M AFRAID SO."


def test_speaker_change_on_new_line_stays_a_hard_break():
    doc = doc_with([[Segment(text="Ja?", style_id="a")], [Segment(text="Nein.", style_id="b")]])
    out = translate_document(doc, FakeTranslator(), "en").document.cues[0]
    assert out.text == "JA?\nNEIN."


def test_placeholder_cues_are_not_sent():
    doc = doc_with([[Segment(text=".")]], [[Segment(text="Hallo")]], [[Segment(text=" - ")]])
    fake = FakeTranslator()
    out = translate_document(doc, fake, "en").document
    assert [c.id for c in fake.requests[0].cues] == ["2"]
    assert [c.text for c in out.cues] == [".", "HALLO", " - "]


def test_batches_and_context():
    doc = load("sample.de.vtt")
    fake = FakeTranslator()
    translate_document(doc, fake, "en", batch_size=5, context=2)
    assert [len(r.cues) for r in fake.requests] == [5, 5, 3]
    assert fake.requests[0].context == []
    ctx = fake.requests[1].context
    assert [c.id for c in ctx] == ["4", "5"]
    assert ctx[1].segments == ["* Regen prasselt gegen das Fenster *"]
    assert ctx[1].translation == ["* REGEN PRASSELT GEGEN DAS FENSTER *"]
    assert translate_document(doc, FakeTranslator(), "en", context=0).document.cues[0].text
    no_ctx = FakeTranslator()
    translate_document(doc, no_ctx, "en", batch_size=5, context=0)
    assert all(r.context == [] for r in no_ctx.requests)


def test_source_language_override():
    fake = FakeTranslator()
    translate_document(load("sample.de.vtt"), fake, "en", source_language="de-AT")
    assert fake.requests[0].source_language == "de-AT"
    fake = FakeTranslator()
    translate_document(load("sample.en.srt"), fake, "de")
    assert fake.requests[0].source_language is None  # SRT has no language: model detects it


def test_bad_cue_is_retried_alone():
    def fn(request, call):
        if call == 1:  # drop a segment of cue 8 in the first batch
            cues = [CueText(id=c.id, segments=[s.upper() for s in c.segments]) for c in request.cues]
            cues[7] = CueText(id="8", segments=["ONLY ONE"])
            return TranslationResponse(cues=cues)
        return None

    fake = FakeTranslator(fn)
    result = translate_document(load("sample.de.vtt"), fake, "en")
    assert result.warnings == []
    assert [c.id for c in fake.requests[1].cues] == ["8"]
    assert len(fake.requests) == 2
    assert result.document.cues[7].segments()[1].text == " WIR NEHMEN DEN NÄCHSTEN."


def test_invalid_response_retries_each_cue():
    def fn(request, call):
        if call == 1:
            raise InvalidResponseError("garbage")
        return None

    fake = FakeTranslator(fn)
    doc = doc_with([[Segment(text="eins")]], [[Segment(text="zwei")]])
    result = translate_document(doc, fake, "en")
    assert [len(r.cues) for r in fake.requests] == [2, 1, 1]
    assert [c.text for c in result.document.cues] == ["EINS", "ZWEI"]


def test_persistent_segment_mismatch_falls_back_to_one_block():
    def fn(request, call):
        cue = request.cues[0]
        if len(cue.segments) == 2:
            return TranslationResponse(cues=[CueText(id=cue.id, segments=["merged"])])
        return None

    doc = doc_with([[Segment(text="Und jetzt?", style_id="a"), Segment(text=" Gut.", style_id="b")]])
    fake = FakeTranslator(fn)
    result = translate_document(doc, fake, "en", max_attempts=3)
    assert len(fake.requests) == 4  # 3 attempts, then the joined request
    assert fake.requests[-1].cues[0].segments == ["Und jetzt? Gut."]
    assert result.warnings == ["cue 1: segments could not be kept apart; translated as one block"]
    assert [(s.text, s.style_id) for s in result.document.cues[0].segments()] == [("UND JETZT? GUT.", "a")]


def test_cue_left_untranslated_when_everything_fails():
    def fn(request, call):
        return TranslationResponse(cues=[])

    doc = doc_with([[Segment(text="Hallo")]], [[Segment(text="Welt")]])
    result = translate_document(doc, FakeTranslator(fn), "en", max_attempts=2)
    assert [c.text for c in result.document.cues] == ["Hallo", "Welt"]
    assert result.warnings == [
        "cue 1: translation failed; left untranslated",
        "cue 2: translation failed; left untranslated",
    ]


def test_fatal_errors_propagate():
    def fn(request, call):
        raise TranslationError("bad key")

    with pytest.raises(TranslationError, match="bad key"):
        translate_document(load("sample.en.srt"), FakeTranslator(fn), "de")


def test_progress_reports_every_cue():
    calls = []
    translate_document(load("sample.en.srt"), FakeTranslator(), "de", batch_size=3,
                       progress=lambda done, total: calls.append((done, total)))
    assert calls[0] == (0, 7) and calls[-1] == (7, 7)
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)


def test_vtt_voices_and_separator_survive():
    out = translate_document(load("sample.en.vtt"), FakeTranslator(), "de").document
    cue = out.cues[3]
    assert [(s.text, s.voice) for s in cue.segments()] == [
        ("SO WHAT NOW?", "Anna"),
        (" ", None),
        ("WE TAKE THE NEXT ONE.", "Tom"),
    ]
    written = formats.get("vtt").write(out).text
    assert "Language: de" in written
    assert "<v.anna Anna>SO WHAT NOW?</v> <v.tom Tom>WE TAKE THE NEXT ONE." in written
