"""Entry point for the subtitle-translate command: read → translate → write."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from subtitle_translate import __version__, formats
from subtitle_translate.config import ConfigError, Settings, default_config_path, load_settings
from subtitle_translate.formats import FormatError, SubtitleFormat
from subtitle_translate.translate import OpenRouterTranslator, TranslationError, Translator, translate_document

PROG = "subtitle-translate"

#: Exit codes.
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130

TranslatorFactory = Callable[[Settings], Translator]


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Translate a subtitle file (SRT, WebVTT, EBU-TT-D/TTML) with an LLM via OpenRouter.",
        epilog=f"Settings can also come from environment variables (OPENROUTER_API_KEY, "
        f"SUBTITLE_TRANSLATE_MODEL) or {default_config_path()}.",
    )
    parser.add_argument("file", type=Path, help="subtitle file to translate")
    parser.add_argument("-l", "--language", required=True, help="target language, e.g. en, de, uk, pt-BR")
    parser.add_argument("--source-language", help="source language (default: from the file, else detected)")
    parser.add_argument("-f", "--format", choices=formats.available(),
                        help="output format (default: same as the input)")
    parser.add_argument("-o", "--output", type=Path,
                        help="output file (default: next to the input, e.g. movie.en.srt)")
    parser.add_argument("--force", action="store_true", help="overwrite the output file if it exists")
    parser.add_argument("--model", help="OpenRouter model id")
    parser.add_argument("--api-key", help="OpenRouter API key")
    parser.add_argument("--batch-size", type=int, help="cues per request (default 40)")
    parser.add_argument("--context", type=int, help="preceding cues sent as context (default 5)")
    parser.add_argument("--max-line-length", type=int, help="maximum characters per line (default 42)")
    parser.add_argument("--config", type=Path, help="config file (TOML)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def default_output_path(source: Path, language: str, in_fmt: SubtitleFormat, out_fmt: SubtitleFormat) -> Path:
    """``movie.de.vtt`` → ``movie.de.en.vtt``; the extension follows the output format.

    When the format is unchanged the input's own extension is kept (ARD ships
    TTML as ``.vtt``); otherwise the output format's preferred one is used.
    """
    suffix = source.suffix if out_fmt is in_fmt and source.suffix else out_fmt.extensions[0]
    return source.with_name(f"{source.stem}.{language}{suffix}")


def _read_text(path: Path) -> str:
    if not path.exists():
        raise CliError(f"file not found: {path}")
    if not path.is_file():
        raise CliError(f"not a file: {path}")
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CliError(f"{path} is not UTF-8 text ({e.reason} at byte {e.start}); convert it to UTF-8 first") from None


def _openrouter(settings: Settings) -> Translator:
    return OpenRouterTranslator(api_key=settings.require_api_key(), model=settings.model, base_url=settings.base_url)


class _Progress:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.live = stream.isatty()

    def __call__(self, done: int, total: int) -> None:
        if self.live:
            end = "\n" if done == total else ""
            print(f"\r  translated {done}/{total} cues", end=end, file=self.stream, flush=True)


def run(args: argparse.Namespace, translator_factory: TranslatorFactory, err: TextIO) -> Path:
    try:
        settings = load_settings(
            cli={
                "api_key": args.api_key,
                "model": args.model,
                "batch_size": args.batch_size,
                "context": args.context,
                "max_line_length": args.max_line_length,
            },
            config_path=args.config,
        )
    except ConfigError as e:
        raise CliError(str(e), EXIT_USAGE) from None

    text = _read_text(args.file)
    try:
        in_fmt = formats.detect(text, args.file)
        doc = in_fmt.read(text)
    except FormatError as e:
        raise CliError(str(e)) from None
    out_fmt = formats.get(args.format) if args.format else in_fmt
    output = args.output or default_output_path(args.file, args.language, in_fmt, out_fmt)
    if output.exists() and not args.force:
        raise CliError(f"{output} already exists; use --force to overwrite it")

    try:
        translator = translator_factory(settings)
    except ConfigError as e:
        raise CliError(str(e), EXIT_USAGE) from None
    print(f"{args.file.name}: {len(doc.cues)} cues ({in_fmt.name}"
          f"{', ' + doc.language if doc.language else ''}) → {args.language} with {settings.model}",
          file=err)
    try:
        result = translate_document(
            doc,
            translator,
            args.language,
            source_language=args.source_language,
            batch_size=settings.batch_size,
            context=settings.context,
            max_line_length=settings.max_line_length,
            progress=_Progress(err),
        )
    except TranslationError as e:
        raise CliError(str(e)) from None

    written = out_fmt.write(result.document)
    for warning in [*result.warnings, *written.warnings]:
        print(f"warning: {warning}", file=err)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        f.write(written.text)
    return output


def main(argv: Sequence[str] | None = None, translator_factory: TranslatorFactory = _openrouter) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = run(args, translator_factory, sys.stderr)
    except CliError as e:
        print(f"{PROG}: error: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    print(output)
    return EXIT_OK
