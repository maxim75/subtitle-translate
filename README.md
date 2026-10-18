# subtitle-translate

Cross-platform command line utility for translating subtitle files.
Pure Python, no third-party runtime dependencies; works on Linux, macOS and Windows (Python 3.9+).

> Status: first iteration — the tool reads a subtitle file and reports its name and size.

## Install

The recommended way is an isolated tool install, which puts `subtitle-translate` on your `PATH`:

```bash
uv tool install git+https://github.com/maxim75/subtitle-translate
```

or with pipx:

```bash
pipx install git+https://github.com/maxim75/subtitle-translate
```

From a local checkout:

```bash
uv tool install .
```

Plain pip works too (`pip install .`). On Windows, use the same commands in PowerShell;
if `subtitle-translate` is not found afterwards, run `uv tool update-shell` or `pipx ensurepath`.

## Usage

```bash
subtitle-translate movie.en.srt
```

```
movie.en.srt: 1534 bytes
```

You can also run it as a module: `python -m subtitle_translate movie.en.srt`.

Exit codes: `0` success, `1` file missing or not a regular file, `2` bad arguments.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```
