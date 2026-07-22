from __future__ import annotations

import argparse
import sys
from pathlib import Path

from disco_virb_converter import __version__
from disco_virb_converter.gui import run_gui
from disco_virb_converter.outputs import convert_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disco-virb-converter",
        description="Convert Parrot Disco PUD/FreeFlight exports to Garmin VIRB FIT, GPX, and CSV.",
    )
    parser.add_argument("input", nargs="?", help="FreeFlight ZIP, .pud, .json, .txt, or .gz input")
    parser.add_argument("--out", help="Output folder. Default is <flight>_virb_export beside the input.")
    parser.add_argument("--offset-seconds", type=float, default=0.0, help="Shift output timestamps for video sync.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        run_gui()
        return 0

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.input:
        run_gui()
        return 0

    try:
        result = convert_file(Path(args.input), out_dir=args.out, offset_seconds=args.offset_seconds)
    except Exception as exc:  # noqa: BLE001 - CLI should print concise failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Output folder: {result.output_dir}")
    print(f"FIT: {result.fit_path}")
    print(f"GPX: {result.gpx_path}")
    print(f"CSV: {result.csv_path}")
    print(f"Samples: {len(result.rows)}  Skipped: {result.skipped_rows}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0
