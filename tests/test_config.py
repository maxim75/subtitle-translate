from pathlib import Path

import pytest

from subtitle_translate.config import (
    DEFAULT_MODEL,
    ConfigError,
    Settings,
    default_config_path,
    load_settings,
)


@pytest.fixture
def config_file(tmp_path):
    def write(text):
        path = tmp_path / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    return write


def test_defaults_without_any_source(tmp_path):
    s = load_settings(env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert s == Settings()
    assert s.model == DEFAULT_MODEL == "anthropic/claude-sonnet-5.5"
    assert (s.batch_size, s.context, s.max_line_length) == (40, 5, 42)
    assert s.base_url == "https://openrouter.ai/api/v1"


def test_precedence_flag_over_env_over_file(config_file):
    path = config_file('api_key = "from-file"\nmodel = "file/model"\nbatch_size = 10\n')
    env = {"OPENROUTER_API_KEY": "from-env", "SUBTITLE_TRANSLATE_MODEL": "env/model"}
    s = load_settings(cli={"model": "flag/model", "api_key": None}, config_path=path, env=env)
    assert s.model == "flag/model"
    assert s.api_key == "from-env"
    assert s.batch_size == 10


def test_file_used_when_no_flag_or_env(config_file):
    path = config_file('api_key = "from-file"\nmax_line_length = 37\n')
    s = load_settings(config_path=path, env={})
    assert (s.api_key, s.max_line_length) == ("from-file", 37)


def test_default_location_is_read(tmp_path):
    cfg = tmp_path / "subtitle-translate" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('model = "x/y"\n', encoding="utf-8")
    assert load_settings(env={"XDG_CONFIG_HOME": str(tmp_path)}).model == "x/y"


def test_empty_env_values_are_ignored(tmp_path):
    s = load_settings(env={"XDG_CONFIG_HOME": str(tmp_path), "SUBTITLE_TRANSLATE_MODEL": ""})
    assert s.model == DEFAULT_MODEL


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("win32", {"APPDATA": r"C:\Users\me\AppData\Roaming"},
         Path(r"C:\Users\me\AppData\Roaming") / "subtitle-translate" / "config.toml"),
        ("win32", {}, Path("/home/me/AppData/Roaming/subtitle-translate/config.toml")),
        ("linux", {"XDG_CONFIG_HOME": "/xdg"}, Path("/xdg/subtitle-translate/config.toml")),
        ("linux", {}, Path("/home/me/.config/subtitle-translate/config.toml")),
        ("darwin", {}, Path("/home/me/.config/subtitle-translate/config.toml")),
    ],
)
def test_default_config_path_per_platform(platform, env, expected):
    assert default_config_path(env=env, platform=platform, home=Path("/home/me")) == expected


def test_explicit_config_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="config file not found"):
        load_settings(config_path=tmp_path / "missing.toml", env={})


def test_invalid_toml(config_file):
    path = config_file("model = \n")
    with pytest.raises(ConfigError, match=r"invalid TOML in .*config\.toml"):
        load_settings(config_path=path, env={})


def test_unknown_key_is_reported(config_file):
    path = config_file('modle = "typo"\n')
    with pytest.raises(ConfigError, match="unknown setting.*modle.*valid: api_key"):
        load_settings(config_path=path, env={})


@pytest.mark.parametrize(
    ("cli", "match"),
    [
        ({"batch_size": 0}, "batch_size"),
        ({"context": -1}, "context"),
        ({"max_line_length": 5}, "max_line_length"),
    ],
)
def test_invalid_values(cli, match, tmp_path):
    with pytest.raises(ConfigError, match=f"invalid settings.*{match}"):
        load_settings(cli=cli, env={"XDG_CONFIG_HOME": str(tmp_path)})


def test_wrong_type_in_file(config_file):
    path = config_file('batch_size = "many"\n')
    with pytest.raises(ConfigError, match="batch_size"):
        load_settings(config_path=path, env={})


def test_require_api_key():
    assert Settings(api_key="k").require_api_key() == "k"
    with pytest.raises(ConfigError, match="--api-key.*OPENROUTER_API_KEY.*config.toml"):
        Settings().require_api_key()
