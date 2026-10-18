"""Entry point for the subtitle-translate command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from subtitle_translate import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitle-translate",
        description="Translate subtitle files.",
    )
    parser.add_argument("file", type=Path, help="path to the subtitle file")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path: Path = args.file

    if not path.exists():
        print(f"subtitle-translate: error: file not found: {path}", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"subtitle-translate: error: not a file: {path}", file=sys.stderr)
        return 1

    print(f"{path.name}: {path.stat().st_size} bytes")
    return 0
