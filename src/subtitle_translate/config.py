"""Settings resolution: CLI flag > environment variable > config file > default.

Config file (TOML, all keys optional)::

    api_key = "sk-or-..."
    model = "anthropic/claude-sonnet-5.5"
    batch_size = 40
    context = 5
    max_line_length = 42

Location: ``%APPDATA%\\subtitle-translate\\config.toml`` on Windows,
``$XDG_CONFIG_HOME/subtitle-translate/config.toml`` (default ``~/.config``)
elsewhere, or the path given with ``--config``.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

APP_NAME = "subtitle-translate"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_LINE_LENGTH = 42

#: Setting name -> environment variable.
ENV_VARS = {
    "api_key": "OPENROUTER_API_KEY",
    "model": "SUBTITLE_TRANSLATE_MODEL",
}


class ConfigError(Exception):
    """Configuration is missing or invalid."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    batch_size: int = Field(default=40, ge=1)
    context: int = Field(default=5, ge=0)
    max_line_length: int = Field(default=DEFAULT_MAX_LINE_LENGTH, ge=10)

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "no OpenRouter API key: pass --api-key, set "
                f"{ENV_VARS['api_key']}, or add api_key to {default_config_path()}"
            )
        return self.api_key


def default_config_path(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if platform == "win32":
        base = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    return base / APP_NAME / "config.toml"


def _read_file(path: Path, required: bool) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise ConfigError(f"config file not found: {path}")
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from None
    unknown = set(data) - set(Settings.model_fields)
    if unknown:
        raise ConfigError(
            f"unknown setting(s) in {path}: {', '.join(sorted(unknown))}; "
            f"valid: {', '.join(Settings.model_fields)}"
        )
    return data


def load_settings(
    cli: Mapping[str, Any] | None = None,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Merge all sources. ``None`` CLI values mean "not given"."""
    env = os.environ if env is None else env
    path = config_path or default_config_path(env)
    values: dict[str, Any] = _read_file(path, required=config_path is not None)
    for name, var in ENV_VARS.items():
        if env.get(var):
            values[name] = env[var]
    values.update({k: v for k, v in (cli or {}).items() if v is not None})
    try:
        return Settings(**values)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"invalid settings ({problems})") from None
