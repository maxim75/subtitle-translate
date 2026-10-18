"""Colour normalisation shared by all formats.

Inside the object model every colour is a lowercase ``#rrggbb`` or
``#rrggbbaa`` string. Formats convert to and from their own notation.
"""

from __future__ import annotations

import re

# CSS / HTML basic colour keywords plus a few common extras. SRT and WebVTT
# files in the wild mostly use these.
NAMED_COLORS: dict[str, str] = {
    "black": "#000000",
    "silver": "#c0c0c0",
    "gray": "#808080",
    "grey": "#808080",
    "white": "#ffffff",
    "maroon": "#800000",
    "red": "#ff0000",
    "purple": "#800080",
    "fuchsia": "#ff00ff",
    "magenta": "#ff00ff",
    "green": "#008000",
    "lime": "#00ff00",
    "olive": "#808000",
    "yellow": "#ffff00",
    "navy": "#000080",
    "blue": "#0000ff",
    "teal": "#008080",
    "aqua": "#00ffff",
    "cyan": "#00ffff",
    "orange": "#ffa500",
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def normalize_color(value: str) -> str:
    """Return ``value`` as ``#rrggbb`` / ``#rrggbbaa``; raise ValueError if unknown."""
    v = value.strip()
    named = NAMED_COLORS.get(v.lower())
    if named:
        return named
    m = _HEX.match(v)
    if not m:
        raise ValueError(f"unsupported colour: {value!r}")
    digits = m.group(1).lower()
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return f"#{digits}"


def color_name(value: str) -> str | None:
    """Return a keyword for an exact ``#rrggbb`` match, else None."""
    v = value.lower()[:7]
    for name, hex_value in NAMED_COLORS.items():
        if hex_value == v:
            return name
    return None
