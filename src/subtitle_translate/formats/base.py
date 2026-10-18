"""Contract every subtitle format implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field

from subtitle_translate.model import SubtitleDocument


class FormatError(Exception):
    """A file cannot be detected, parsed or written."""


@dataclass(frozen=True)
class WriteResult:
    text: str
    warnings: list[str] = field(default_factory=list)


class DroppedFeatures:
    """Collects features a writer could not represent, one warning per kind."""

    def __init__(self, format_label: str) -> None:
        self._label = format_label
        self._counts: Counter[str] = Counter()

    def add(self, feature: str) -> None:
        self._counts[feature] += 1

    def warnings(self) -> list[str]:
        return [
            f"{self._label} cannot store {feature}; dropped on {n} cue{'s' if n != 1 else ''}"
            for feature, n in sorted(self._counts.items())
        ]


class SubtitleFormat(ABC):
    """A subtitle file format. Register implementations with ``@register``."""

    #: Short identifier used by ``--format`` (e.g. ``"srt"``).
    name: str
    #: File extensions, preferred one first, including the dot.
    extensions: tuple[str, ...]

    @abstractmethod
    def sniff(self, text: str) -> bool:
        """Return True if ``text`` looks like this format (content only)."""

    @abstractmethod
    def read(self, text: str) -> SubtitleDocument:
        """Parse ``text``; raise :class:`FormatError` on malformed input."""

    @abstractmethod
    def write(self, doc: SubtitleDocument) -> WriteResult:
        """Serialise ``doc``, reporting anything that could not be kept."""
