from subtitle_translate.model import Line, Segment
from subtitle_translate.wrap import DEFAULT_MAX_LINE_LENGTH, flow, wrap_lines


def texts(lines):
    return [ln.text for ln in lines]


def parts(lines):
    return [[(s.text, s.style_id, s.voice) for s in ln.segments] for ln in lines]


def test_default_is_42():
    assert DEFAULT_MAX_LINE_LENGTH == 42


def test_short_lines_untouched():
    line = Line(segments=[Segment(text="  odd   spacing kept ", style_id="a")])
    assert wrap_lines([line], 42)[0] is line


def test_long_line_split_balanced():
    line = Line(segments=[Segment(text="Attention, platform four: the train to Munich is running late.")])
    out = wrap_lines([line], 42)
    assert texts(out) == ["Attention, platform four: the", "train to Munich is running late."]
    assert all(len(t) <= 42 for t in texts(out))


def test_very_long_line_uses_more_lines():
    words = " ".join(["word"] * 30)  # 149 chars
    out = wrap_lines([Line(segments=[Segment(text=words)])], 42)
    assert len(out) == 4
    assert all(len(t) <= 42 for t in texts(out))


def test_styles_and_voices_follow_their_words():
    line = Line(segments=[
        Segment(text="So what are we going to do now? ", style_id="yellow", voice="Anna"),
        Segment(text="We will simply take the next one.", style_id="green", voice="Tom"),
    ])
    out = wrap_lines([line], 42)
    # Breaking at the speaker change is preferred over a slightly better balance.
    assert parts(out) == [
        [("So what are we going to do now?", "yellow", "Anna")],
        [("We will simply take the next one.", "green", "Tom")],
    ]


def test_segment_split_across_lines_keeps_style_on_both():
    line = Line(segments=[
        Segment(text="Short. ", style_id="a"),
        Segment(text="This one is a much longer segment that must be split", style_id="b"),
    ])
    out = wrap_lines([line], 42)
    assert all(len(t) <= 42 for t in texts(out))
    styles = [s.style_id for ln in out for s in ln.segments]
    assert styles[0] == "a" and set(styles[1:]) == {"b"}
    assert " ".join(texts(out)) == line.text


def test_punctuation_from_another_segment_stays_attached():
    segs = [Segment(text="x" * 30, style_id="a"), Segment(text=", and more words here", style_id="b")]
    out = flow(segs, 2, 42)
    assert texts(out)[0].endswith(",")


def test_flow_restores_original_line_count():
    seg = Segment(text="I know. The tram was stuck in traffic for twenty minutes.", style_id="S5")
    out = flow([seg], 2, 42)
    assert texts(out) == ["I know. The tram was stuck in", "traffic for twenty minutes."]
    assert all(s.style_id == "S5" for ln in out for s in ln.segments)


def test_flow_never_creates_empty_lines():
    assert texts(flow([Segment(text="Yes.")], 3, 42)) == ["Yes."]
    assert texts(flow([Segment(text="   ")], 2, 42)) == [""]


def test_flow_adds_lines_when_needed():
    out = flow([Segment(text="one two three four five six seven eight nine ten")], 1, 20)
    assert len(out) == 3
    assert all(len(t) <= 20 for t in texts(out))


def test_overlong_single_word_is_not_split():
    out = wrap_lines([Line(segments=[Segment(text="Donaudampfschifffahrtsgesellschaft ok")])], 20)
    assert texts(out) == ["Donaudampfschifffahrtsgesellschaft", "ok"]


def test_cjk_breaks_between_characters():
    text = "私たちは次の電車に乗ります。今度はあなたが払ってください。"
    out = wrap_lines([Line(segments=[Segment(text=text)])], 16)
    assert "".join(texts(out)) == text
    assert all(len(t) <= 16 for t in texts(out))
    assert len(out) == 2


def test_many_words_fall_back_to_greedy():
    words = " ".join(f"w{i}" for i in range(200))
    out = flow([Segment(text=words)], 1, 42)
    assert " ".join(texts(out)) == words
    assert max(len(t) for t in texts(out)) <= 60
