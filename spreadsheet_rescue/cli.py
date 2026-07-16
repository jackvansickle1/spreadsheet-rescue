"""Command-line interface for Spreadsheet Rescue."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .engine import RescueError, rescue_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spreadsheet-rescue",
        description=(
            "Clean a CSV, remove exact duplicates, normalize configured fields, "
            "and produce privacy-conscious change reports."
        ),
    )
    parser.add_argument("input_csv", type=Path, help="CSV file to clean")
    parser.add_argument("-c", "--config", type=Path, help="JSON cleanup configuration")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="output directory (default: <input-stem>_rescue beside the input)",
    )
    parser.add_argument(
        "--output-prefix",
        help="base name for output files (default: input file stem)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="analyze and report counts to the console without writing any files",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress success output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _print_summary(result: object) -> None:
    audit = result.audit
    rows = audit["rows"]
    changes = audit["changes"]
    mode = "DRY RUN — no files written" if result.dry_run else "COMPLETE"
    print(f"Spreadsheet Rescue: {mode}")
    print(f"Rows: {rows['input']} input -> {rows['output']} output")
    print(f"Exact duplicates removed: {rows['exact_duplicates_removed']}")
    print(f"Blank rows removed: {rows['blank_removed']}")
    print(f"Headers renamed: {changes['headers_renamed']}")
    print(f"Whitespace-normalized cells: {changes['whitespace_cells']}")
    print(f"Typed values normalized: {changes['typed_values_changed']}")
    print(f"Warnings: {len(audit['warnings'])}")
    for warning in audit["warnings"]:
        print(f"  - {warning}")
    if not result.dry_run:
        print(f"Cleaned CSV: {result.cleaned_path}")
        print(f"JSON audit: {result.json_report_path}")
        print(f"Markdown report: {result.markdown_report_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = rescue_csv(
            args.input_csv,
            config_path=args.config,
            output_dir=args.output_dir,
            output_prefix=args.output_prefix,
            dry_run=args.dry_run,
        )
    except RescueError as exc:
        print(f"Spreadsheet Rescue error: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
