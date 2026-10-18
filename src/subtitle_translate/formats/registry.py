"""Registry of available subtitle formats and content-based detection."""

from __future__ import annotations

from pathlib import Path

from subtitle_translate.formats.base import FormatError, SubtitleFormat

_FORMATS: dict[str, SubtitleFormat] = {}


def register(cls: type[SubtitleFormat]) -> type[SubtitleFormat]:
    """Class decorator: instantiate and register a format."""
    fmt = cls()
    if fmt.name in _FORMATS:
        raise ValueError(f"subtitle format {fmt.name!r} is already registered")
    _FORMATS[fmt.name] = fmt
    return cls


def unregister(name: str) -> None:
    _FORMATS.pop(name, None)


def available() -> list[str]:
    return sorted(_FORMATS)


def get(name: str) -> SubtitleFormat:
    try:
        return _FORMATS[name]
    except KeyError:
        raise FormatError(
            f"unknown format {name!r}; available: {', '.join(available()) or 'none'}"
        ) from None


def detect(text: str, path: str | Path | None = None) -> SubtitleFormat:
    """Pick the format by content; the file extension only breaks ties.

    Extensions are unreliable (ARD ships TTML in ``.vtt`` files), so a format
    is only chosen when its ``sniff`` accepts the content.
    """
    matches = [f for f in _FORMATS.values() if f.sniff(text)]
    if len(matches) == 1:
        return matches[0]
    where = f" {Path(path).name}" if path else ""
    if not matches:
        raise FormatError(f"could not detect subtitle format of{where or ' input'}")
    suffix = Path(path).suffix.lower() if path else ""
    by_ext = [f for f in matches if suffix in f.extensions]
    if len(by_ext) == 1:
        return by_ext[0]
    names = ", ".join(sorted(f.name for f in matches))
    raise FormatError(f"ambiguous subtitle format of{where or ' input'} ({names}); use --format")
