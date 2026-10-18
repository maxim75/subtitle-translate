"""Format-neutral object model for subtitle documents.

Readers turn a file into a :class:`SubtitleDocument`, the translator rewrites
segment text, and writers turn the document back into a file. Anything only a
single format understands is kept in that format's slot of ``extensions`` so
same-format round trips stay close to lossless.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from subtitle_translate.colors import normalize_color


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Style(_Model):
    """Visual style. ``None`` means "not specified / inherit"."""

    color: str | None = None
    background_color: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_family: str | None = None
    font_size: str | None = None
    text_align: Literal["left", "center", "right", "start", "end"] | None = None

    @field_validator("color", "background_color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return None if v is None else normalize_color(v)

    def merged(self, override: Style | None) -> Style:
        """Return a copy where fields set on ``override`` win."""
        if override is None:
            return self.model_copy()
        data = self.model_dump()
        data.update(override.model_dump(exclude_none=True))
        return Style(**data)

    def is_empty(self) -> bool:
        return not self.model_dump(exclude_none=True)


class Region(_Model):
    """Screen area a cue is rendered in, in percent of the video frame."""

    origin: tuple[float, float] | None = None
    extent: tuple[float, float] | None = None
    display_align: Literal["before", "center", "after"] | None = None


class Segment(_Model):
    """A run of text with one style (and optionally one speaker)."""

    text: str
    style_id: str | None = None
    voice: str | None = None


class Line(_Model):
    segments: list[Segment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


class Cue(_Model):
    id: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    region_id: str | None = None
    style_id: str | None = None
    lines: list[Line] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _timing(self) -> Cue:
        if self.end_ms < self.start_ms:
            raise ValueError(f"cue {self.id!r} ends ({self.end_ms}) before it starts ({self.start_ms})")
        return self

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def segments(self) -> list[Segment]:
        return [s for line in self.lines for s in line.segments]


class SubtitleDocument(_Model):
    source_format: str | None = None
    language: str | None = None
    styles: dict[str, Style] = Field(default_factory=dict)
    regions: dict[str, Region] = Field(default_factory=dict)
    cues: list[Cue] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _references(self) -> SubtitleDocument:
        for cue in self.cues:
            if cue.region_id is not None and cue.region_id not in self.regions:
                raise ValueError(f"cue {cue.id!r} references unknown region {cue.region_id!r}")
            for style_id in [cue.style_id, *(s.style_id for s in cue.segments())]:
                if style_id is not None and style_id not in self.styles:
                    raise ValueError(f"cue {cue.id!r} references unknown style {style_id!r}")
        return self

    def resolve_style(self, cue: Cue, segment: Segment | None = None) -> Style:
        """Effective style of ``segment`` inside ``cue`` (segment overrides cue)."""
        style = self.styles.get(cue.style_id, Style()) if cue.style_id else Style()
        if segment is not None and segment.style_id:
            style = style.merged(self.styles[segment.style_id])
        return style
