import subprocess
import sys

import pytest

from subtitle_translate.cli import main

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello, world!\n"


def test_prints_name_and_size(tmp_path, capsys):
    sub = tmp_path / "movie.en.srt"
    sub.write_bytes(SRT.encode("utf-8"))

    assert main([str(sub)]) == 0
    assert capsys.readouterr().out == f"movie.en.srt: {len(SRT.encode())} bytes\n"


def test_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.srt")]) == 1
    assert "file not found" in capsys.readouterr().err


def test_directory_is_rejected(tmp_path, capsys):
    assert main([str(tmp_path)]) == 1
    assert "not a file" in capsys.readouterr().err


def test_no_argument_shows_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "usage: subtitle-translate" in capsys.readouterr().err


def test_runs_as_module(tmp_path):
    sub = tmp_path / "a.vtt"
    sub.write_text("WEBVTT\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "subtitle_translate", str(sub)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "a.vtt: 7 bytes"
