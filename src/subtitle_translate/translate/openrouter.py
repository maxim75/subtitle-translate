"""Translator backed by any OpenRouter model (OpenAI-compatible API)."""

from __future__ import annotations

import json
import re
from typing import Any

import openai
from pydantic import ValidationError

from subtitle_translate.translate.base import (
    InvalidResponseError,
    TranslationError,
    TranslationRequest,
    TranslationResponse,
)
from subtitle_translate.translate.prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, user_message

APP_URL = "https://github.com/maxim75/subtitle-translate"
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class OpenRouterTranslator:
    """Sends one chat completion per request and parses the JSON answer.

    Network errors, timeouts, rate limits and 5xx responses are retried by
    the openai client with exponential backoff (``max_retries``).
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        *,
        timeout: float = 180.0,
        max_retries: int = 5,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._client = client or openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={"HTTP-Referer": APP_URL, "X-Title": "subtitle-translate"},
        )
        self._structured = True  # switched off if the model rejects response_format

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message(request)},
        ]
        while True:
            try:
                return self._parse(self._create(messages))
            except openai.BadRequestError as e:
                if self._structured and "response_format" in str(e):
                    self._structured = False  # model lacks structured outputs; rely on the prompt
                    continue
                raise TranslationError(f"OpenRouter rejected the request: {e}") from None
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                raise TranslationError(f"OpenRouter authentication failed: {e}") from None
            except openai.NotFoundError as e:
                raise TranslationError(f"unknown OpenRouter model {self.model!r}: {e}") from None
            except openai.APIError as e:
                raise TranslationError(f"OpenRouter request failed: {e}") from None

    def _create(self, messages: list[dict]) -> Any:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self._structured:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "subtitle_translation", "strict": True, "schema": RESPONSE_SCHEMA},
            }
        return self._client.chat.completions.create(**kwargs)

    @staticmethod
    def _parse(completion: Any) -> TranslationResponse:
        if not completion.choices:
            raise InvalidResponseError("empty response from model")
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            raise InvalidResponseError("response was cut off (output token limit); try a smaller --batch-size")
        content = choice.message.content or ""
        if m := _FENCE.match(content):
            content = m.group(1)
        try:
            return TranslationResponse.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as e:
            raise InvalidResponseError(f"model did not return the expected JSON: {e}") from None
