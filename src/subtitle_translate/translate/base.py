"""Translator contract and the JSON messages exchanged with it."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class TranslationError(Exception):
    """Translation failed and retrying will not help (e.g. bad API key)."""


class InvalidResponseError(TranslationError):
    """The model answered, but not with usable JSON; worth retrying."""


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CueText(_Message):
    """One cue as sent to / returned by the model: its segments, in order."""

    id: str
    segments: list[str]


class ContextCue(CueText):
    """A preceding cue, for consistency only; never translated again."""

    translation: list[str] | None = None


class TranslationRequest(_Message):
    source_language: str | None = None  # None: let the model detect it
    target_language: str
    context: list[ContextCue] = Field(default_factory=list)
    cues: list[CueText]


class TranslationResponse(_Message):
    cues: list[CueText]


class Translator(Protocol):
    """Anything that turns a request into a response, e.g. an LLM client."""

    def translate(self, request: TranslationRequest) -> TranslationResponse: ...
