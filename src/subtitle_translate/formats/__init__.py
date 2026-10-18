"""Subtitle formats.

To add a format: create a module with a :class:`SubtitleFormat` subclass
decorated with ``@register`` and import it at the bottom of this file.
"""

from subtitle_translate.formats.base import (
    DroppedFeatures,
    FormatError,
    StyleTable,
    SubtitleFormat,
    WriteResult,
)
from subtitle_translate.formats.registry import available, detect, get, register, unregister

__all__ = [
    "DroppedFeatures",
    "FormatError",
    "StyleTable",
    "SubtitleFormat",
    "WriteResult",
    "available",
    "detect",
    "get",
    "register",
    "unregister",
]

# Built-in formats register themselves on import.
from subtitle_translate.formats import srt  # noqa: E402,F401
from subtitle_translate.formats import webvtt  # noqa: E402,F401
