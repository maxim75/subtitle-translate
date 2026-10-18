import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

from subtitle_translate import formats
from subtitle_translate.config import DEFAULT_MODEL
from subtitle_translate.translate import (
    CueText,
    InvalidResponseError,
    OpenRouterTranslator,
    TranslationError,
    TranslationRequest,
    translate_document,
)

REQUEST = TranslationRequest(
    source_language="de",
    target_language="en",
    cues=[CueText(id="1", segments=["Und jetzt?", "Wir nehmen den nächsten."])],
)
ANSWER = {"cues": [{"id": "1", "segments": ["And now?", "We take the next one."]}]}


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))])


def status_error(cls, code, message="boom"):
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return cls(message, response=httpx2.Response(code, request=request), body=None)


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def translator(*results):
    client = FakeClient(*results)
    return OpenRouterTranslator(api_key="k", model="some/model", client=client), client


def test_request_shape():
    t, client = translator(completion(json.dumps(ANSWER)))
    response = t.translate(REQUEST)
    assert response.cues[0].segments == ["And now?", "We take the next one."]
    call = client.calls[0]
    assert call["model"] == "some/model"
    system, user = call["messages"]
    assert system["role"] == "system" and "as many segments" in system["content"]
    assert json.loads(user["content"]) == {
        "source_language": "de",
        "target_language": "en",
        "context": [],
        "cues": [{"id": "1", "segments": ["Und jetzt?", "Wir nehmen den nächsten."]}],
    }
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert "temperature" not in call


def test_code_fences_are_stripped():
    t, _ = translator(completion("```json\n" + json.dumps(ANSWER) + "\n```"))
    assert t.translate(REQUEST).cues[0].id == "1"


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (completion("not json"), "expected JSON"),
        (completion(json.dumps({"cues": [{"id": 1}]})), "expected JSON"),
        (completion(None), "expected JSON"),
        (completion(json.dumps(ANSWER), finish_reason="length"), "cut off"),
        (SimpleNamespace(choices=[]), "empty response"),
    ],
)
def test_unusable_answers_are_retryable(result, match):
    t, _ = translator(result)
    with pytest.raises(InvalidResponseError, match=match):
        t.translate(REQUEST)


def test_falls_back_when_model_lacks_structured_outputs():
    t, client = translator(
        status_error(openai.BadRequestError, 400, "response_format json_schema is not supported"),
        completion(json.dumps(ANSWER)),
        completion(json.dumps(ANSWER)),
    )
    t.translate(REQUEST)
    t.translate(REQUEST)
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]
    assert "response_format" not in client.calls[2]  # remembered


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (status_error(openai.BadRequestError, 400, "context too long"), "rejected the request"),
        (status_error(openai.AuthenticationError, 401), "authentication failed"),
        (status_error(openai.PermissionDeniedError, 403), "authentication failed"),
        (status_error(openai.NotFoundError, 404), "unknown OpenRouter model 'some/model'"),
        (status_error(openai.RateLimitError, 429), "request failed"),
        (openai.APIConnectionError(request=httpx2.Request("POST", "https://x")), "request failed"),
    ],
)
def test_api_errors_become_fatal_translation_errors(error, match):
    t, _ = translator(error)
    with pytest.raises(TranslationError, match=match) as exc:
        t.translate(REQUEST)
    assert not isinstance(exc.value, InvalidResponseError)


def test_real_client_configuration():
    t = OpenRouterTranslator(api_key="sk-test", model="m", max_retries=7, timeout=12)
    client = t._client
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert client.max_retries == 7
    assert client.default_headers["X-Title"] == "subtitle-translate"
    assert client.default_headers["HTTP-Referer"].startswith("https://github.com/")


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_live_translation_of_sample():
    text = (Path(__file__).parent.parent / "examples" / "sample.de.vtt").read_text(encoding="utf-8")
    doc = formats.get("ttml").read(text)
    doc.cues = doc.cues[5:8]
    model = os.environ.get("SUBTITLE_TRANSLATE_MODEL", DEFAULT_MODEL)
    t = OpenRouterTranslator(api_key=os.environ["OPENROUTER_API_KEY"], model=model)
    result = translate_document(doc, t, "en")
    assert result.warnings == []
    c8 = result.document.cues[2]
    assert [s.style_id for s in c8.segments()] == ["S4", "S5"]
    assert all(seg.text.strip() for seg in c8.segments())
