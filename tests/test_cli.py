import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from subtitle_translate import formats
from subtitle_translate.cli import main
from subtitle_translate.translate import CueText, TranslationError, TranslationResponse

EXAMPLES = Path(__file__).parent.parent / "examples"


class Upper:
    def __init__(self):
        self.requests = []

    def translate(self, request):
        self.requests.append(request)
        return TranslationResponse(cues=[CueText(id=c.id, segments=[s.upper() for s in c.segments]) for c in request.cues])


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """No real config file or environment settings leak into tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SUBTITLE_TRANSLATE_MODEL", raising=False)


@pytest.fixture
def workdir(tmp_path):
    for name in ("sample.en.srt", "sample.en.vtt", "sample.de.vtt"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    return tmp_path


def run(argv, translator=None):
    translator = translator or Upper()
    seen = {}

    def factory(settings):
        seen["settings"] = settings
        return translator

    return main([str(a) for a in argv], translator_factory=factory), seen


def test_translates_ttml_in_vtt_file_keeping_format_and_extension(workdir, capsys):
    code, seen = run([workdir / "sample.de.vtt", "--language", "en"])
    assert code == 0
    out = workdir / "sample.de.en.vtt"
    captured = capsys.readouterr()
    assert captured.out.strip() == str(out)
    assert "sample.de.vtt: 13 cues (ttml, de) → en with anthropic/claude-sonnet-5.5" in captured.err
    text = out.read_text(encoding="utf-8")
    assert formats.detect(text, out).name == "ttml"
    doc = formats.get("ttml").read(text)
    assert doc.language == "en"
    assert [(s.text, s.style_id) for s in doc.cues[7].segments()] == [
        ("UND JETZT?", "S4"), (" WIR NEHMEN DEN NÄCHSTEN.", "S5"),
    ]


@pytest.mark.parametrize(("fmt", "name"), [("srt", "sample.de.en.srt"), ("vtt", "sample.de.en.vtt"),
                                          ("ttml", "sample.de.en.vtt")])
def test_format_option_sets_output_and_extension(workdir, fmt, name):
    code, _ = run([workdir / "sample.de.vtt", "-l", "en", "--format", fmt])
    assert code == 0
    text = (workdir / name).read_text(encoding="utf-8")
    assert formats.detect(text).name == fmt


def test_conversion_warnings_go_to_stderr(workdir, capsys):
    code, _ = run([workdir / "sample.de.vtt", "-l", "en", "-f", "srt"])
    assert code == 0
    err = capsys.readouterr().err
    assert "warning: SRT cannot store background colour; dropped on 13 cues" in err


def test_explicit_output_and_force(workdir, capsys):
    target = workdir / "out" / "x.srt"
    assert run([workdir / "sample.en.srt", "-l", "de", "-o", target])[0] == 0
    assert target.read_text(encoding="utf-8").startswith("1\n00:00:01,000 --> 00:00:03,500\nGOOD MORNING!")
    code, _ = run([workdir / "sample.en.srt", "-l", "de", "-o", target])
    assert code == 1
    assert "already exists; use --force" in capsys.readouterr().err
    assert run([workdir / "sample.en.srt", "-l", "de", "-o", target, "--force"])[0] == 0


def test_existing_output_is_checked_before_translating(workdir):
    (workdir / "sample.en.de.srt").write_text("keep", encoding="utf-8")
    translator = Upper()
    code, _ = run([workdir / "sample.en.srt", "-l", "de"], translator)
    assert code == 1 and translator.requests == []
    assert (workdir / "sample.en.de.srt").read_text(encoding="utf-8") == "keep"


def test_output_uses_unix_newlines(workdir):
    run([workdir / "sample.en.srt", "-l", "de"])
    assert b"\r\n" not in (workdir / "sample.en.de.srt").read_bytes()


def test_cli_settings_override_env_and_config(workdir, monkeypatch, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('model = "file/model"\nbatch_size = 3\ncontext = 1\n', encoding="utf-8")
    monkeypatch.setenv("SUBTITLE_TRANSLATE_MODEL", "env/model")
    translator = Upper()
    code, seen = run([workdir / "sample.en.srt", "-l", "de", "--config", cfg, "--max-line-length", "30",
                      "--source-language", "en-GB"], translator)
    assert code == 0
    s = seen["settings"]
    assert (s.model, s.batch_size, s.context, s.max_line_length) == ("env/model", 3, 1, 30)
    assert [len(r.cues) for r in translator.requests] == [3, 3, 1]
    assert translator.requests[0].source_language == "en-GB"


def test_missing_api_key_is_a_usage_error(workdir, capsys):
    code = main([str(workdir / "sample.en.srt"), "-l", "de"])
    assert code == 2
    assert "no OpenRouter API key" in capsys.readouterr().err


def test_bad_config_is_a_usage_error(workdir, tmp_path, capsys):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("batch_size = 0\n", encoding="utf-8")
    code, _ = run([workdir / "sample.en.srt", "-l", "de", "--config", cfg])
    assert code == 2
    assert "invalid settings" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda d: d / "missing.srt", "file not found"),
        (lambda d: d, "not a file"),
        (lambda d: (d / "x.txt").write_text("hello", encoding="utf-8") and d / "x.txt", "could not detect"),
        (lambda d: (d / "l1.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\ncaf\xe9\n") and d / "l1.srt",
         "not UTF-8"),
    ],
)
def test_input_errors(workdir, capsys, make, message):
    code, _ = run([make(workdir), "-l", "de"])
    assert code == 1
    assert message in capsys.readouterr().err


def test_translation_errors_are_reported(workdir, capsys):
    class Broken:
        def translate(self, request):
            raise TranslationError("OpenRouter authentication failed: 401")

    code, _ = run([workdir / "sample.en.srt", "-l", "de"], Broken())
    assert code == 1
    assert "error: OpenRouter authentication failed" in capsys.readouterr().err
    assert not (workdir / "sample.en.de.srt").exists()


def test_language_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["x.srt"])
    assert exc.value.code == 2
    assert "--language" in capsys.readouterr().err


def test_help_and_version_run_as_module():
    help_out = subprocess.run([sys.executable, "-m", "subtitle_translate", "--help"],
                              capture_output=True, text=True)
    assert help_out.returncode == 0
    assert "{srt,ttml,vtt}" in help_out.stdout
    version = subprocess.run([sys.executable, "-m", "subtitle_translate", "--version"],
                             capture_output=True, text=True)
    assert version.stdout.startswith("subtitle-translate ")
