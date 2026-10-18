# subtitle-translate

Cross-platform command line utility that translates subtitle files with an LLM via
[OpenRouter](https://openrouter.ai), keeping timing, colours, speakers and positions.
Pure Python; works on Linux, macOS and Windows (Python 3.11+).

Supported formats (read and write): **SRT**, **WebVTT** and **EBU-TT-D / TTML**.
The format is detected from the file content, so ARD/ZDF-style TTML files named `.vtt` work.

## Install

The recommended way is an isolated tool install, which puts `subtitle-translate` on your `PATH`:

```bash
uv tool install git+https://github.com/maxim75/subtitle-translate
```

or with pipx:

```bash
pipx install git+https://github.com/maxim75/subtitle-translate
```

From a local checkout: `uv tool install .` (plain `pip install .` works too).
On Windows use the same commands in PowerShell; if `subtitle-translate` is not found
afterwards, run `uv tool update-shell` or `pipx ensurepath`.

## Quick start

```bash
export OPENROUTER_API_KEY=sk-or-...        # PowerShell: $env:OPENROUTER_API_KEY = "sk-or-..."
subtitle-translate Wendezeit.vtt --language en
```

```
Wendezeit.vtt: 936 cues (ttml, de) → en with anthropic/claude-sonnet-5.5
  translated 930/930 cues
Wendezeit.en.vtt
```

The translated file is written next to the input with the target language added
(`movie.de.srt` → `movie.de.en.srt`). Progress and warnings go to stderr; the output
path is printed to stdout.

## Usage

```
subtitle-translate FILE --language LANG [options]
```

| Option | Meaning |
| --- | --- |
| `-l`, `--language` | Target language (required), e.g. `en`, `de`, `uk`, `pt-BR` |
| `--source-language` | Source language. Default: from the file (`xml:lang` in TTML, `Language:` in WebVTT), otherwise detected by the model |
| `-f`, `--format {srt,ttml,vtt}` | Output format. Default: same as the input |
| `-o`, `--output PATH` | Output file. Default: next to the input with the language suffix |
| `--force` | Overwrite an existing output file (checked before anything is sent to the API) |
| `--model` | OpenRouter model id. Default `anthropic/claude-sonnet-5.5` |
| `--api-key` | OpenRouter API key |
| `--batch-size` | Cues per request. Default 40 |
| `--context` | Preceding cues sent as context for consistency. Default 5 |
| `--max-line-length` | Maximum characters per line. Default 42 |
| `--config PATH` | Config file to use instead of the default location |

Examples:

```bash
subtitle-translate movie.srt -l de                        # → movie.de.srt
subtitle-translate Wendezeit.vtt -l en --format srt       # TTML in, SRT out → Wendezeit.en.srt
subtitle-translate talk.vtt -l uk --model anthropic/claude-haiku-5.5 -o out/talk.uk.vtt
```

### Configuration

Every setting is taken from the first place that has it:

1. the command line flag;
2. an environment variable: `OPENROUTER_API_KEY`, `SUBTITLE_TRANSLATE_MODEL`;
3. the config file;
4. the built-in default.

The config file is TOML. Default location: `%APPDATA%\subtitle-translate\config.toml`
on Windows, `$XDG_CONFIG_HOME/subtitle-translate/config.toml` (usually
`~/.config/subtitle-translate/config.toml`) on Linux and macOS. All keys are optional:

```toml
api_key = "sk-or-..."
model = "anthropic/claude-sonnet-5.5"
batch_size = 40
context = 5
max_line_length = 42
```

### What is kept

- **Same format in and out:** everything. The original header, styles, regions, notes and
  cue settings are reused; EBU-TT-D files are edited in place, so only the translated
  text and `xml:lang` change.
- **Converting between formats:** timing, text, line breaks, colours, bold/italic/underline,
  speakers and positions as far as the target format can express them. Anything else is
  dropped with one warning per kind, e.g.
  `warning: SRT cannot store background colour; dropped on 936 cues`.
- **Speaker colours:** a subtitle is split into segments wherever the colour or speaker
  changes, and the model must return the same segments, so two people in one subtitle
  keep their own colours.
- **Line length:** translated text is re-wrapped to `--max-line-length`, keeping the line
  breaks between speakers and dialogue dashes. Words are never split.
- Cues without letters (e.g. a lone `.`) are copied without calling the API.

If the model's answer for a cue cannot be matched to its segments after retries, that cue is
translated as one block (warning), and as a last resort left untranslated (warning).

Input files must be UTF-8 (a BOM is fine).

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Input, format, output or translation error |
| 2 | Invalid arguments or configuration (incl. missing API key) |
| 130 | Interrupted (Ctrl+C) |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest                    # offline; the API is faked
uv run pytest -m live            # real OpenRouter call, needs OPENROUTER_API_KEY
```

### Architecture

```
src/subtitle_translate/
  model.py        format-neutral object model (document → cues → lines → styled segments)
  formats/        one module per format + registry and content-based detection
  translate/      Translator protocol, OpenRouter client, batching/validation pipeline
  wrap.py         line layout of translated text
  config.py       settings: flag > env > config file > default
  cli.py          read → translate → write
```

### Adding a subtitle format

1. Create `src/subtitle_translate/formats/<name>.py` with a subclass of `SubtitleFormat`
   decorated with `@register`: set `name` and `extensions`, implement `sniff(text)`
   (content-based detection), `read(text) -> SubtitleDocument` and
   `write(doc) -> WriteResult` (report anything you drop with `DroppedFeatures`).
   Keep data only your format understands in `doc.extensions["<name>"]` /
   `cue.extensions["<name>"]` so same-format round trips stay lossless.
2. Import the module at the bottom of `formats/__init__.py`.
3. Add an example to `examples/`; the conversion tests in `tests/test_conversion.py`
   pick it up via `SAMPLES`.

### Adding a translation provider

Write a class with `translate(request: TranslationRequest) -> TranslationResponse`
(see `translate/base.py`) and pass it to `translate_document()`.
