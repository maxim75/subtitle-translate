"""Contract every subtitle format implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field

from subtitle_translate.model import Style, SubtitleDocument


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


class StyleTable:
    """Builds ``SubtitleDocument.styles`` while reading, deduplicating styles.

    Ids are ``s1``, ``s2``, ... unless a reader passes its own ``key`` (e.g. the
    original markup) to keep visually equal but differently written styles apart.
    """

    def __init__(self) -> None:
        self.styles: dict[str, Style] = {}
        self._ids: dict[str, str] = {}

    def id_for(self, style: Style, key: str | None = None) -> str | None:
        if style.is_empty() and key is None:
            return None
        key = key if key is not None else style.model_dump_json()
        if key not in self._ids:
            self._ids[key] = f"s{len(self._ids) + 1}"
            self.styles[self._ids[key]] = style
        return self._ids[key]


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
