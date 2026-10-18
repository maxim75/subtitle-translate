"""Translation of subtitle documents.

The pipeline only depends on the :class:`Translator` protocol; to use another
provider, write a class with a ``translate(request) -> response`` method.
"""

from subtitle_translate.translate.base import (
    ContextCue,
    CueText,
    InvalidResponseError,
    TranslationError,
    TranslationRequest,
    TranslationResponse,
    Translator,
)
from subtitle_translate.translate.openrouter import OpenRouterTranslator
from subtitle_translate.translate.pipeline import TranslationResult, translate_document

__all__ = [
    "ContextCue",
    "CueText",
    "InvalidResponseError",
    "OpenRouterTranslator",
    "TranslationError",
    "TranslationRequest",
    "TranslationResponse",
    "TranslationResult",
    "Translator",
    "translate_document",
]
