"""Console entry for ``maxkit-s1-l1`` and ``python -m maxkit_s1``."""

import argparse
import sys
from pathlib import Path

from maxkit_s1.errors import UsageError
from maxkit_s1.runner import run_l1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        print(f"maxkit-s1-l1: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code)
    try:
        outcome = run_l1(
            contract_path=Path(args.contract),
            source_path=Path(args.source),
            deliverable_path=Path(args.deliverable),
            glossary_path=Path(args.glossary) if args.glossary else None,
            out_path=Path(args.out) if args.out else None,
        )
    except UsageError as exc:
        print(f"maxkit-s1-l1: {exc}", file=sys.stderr)
        return 2
    print(outcome.summary)
    return outcome.exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maxkit-s1-l1",
        description=(
            "S1 L1 format-contract check for VO EN→ZH. "
            "Template A: plain lines. Template B: csv with en and zh columns. "
            "Exit 0 on PASS, 1 on FAIL, 2 on usage or IO errors."
        ),
    )
    parser.error = _usage_error(parser)  # type: ignore[method-assign]
    parser.add_argument(
        "--contract", required=True, help="YAML contract, or Markdown with YAML frontmatter"
    )
    parser.add_argument("--source", required=True, help="EN source (.txt, or .csv when template B)")
    parser.add_argument("--deliverable", required=True, help="Bilingual deliverable")
    parser.add_argument("--glossary", help="Optional CSV: term_en,term_zh,required")
    parser.add_argument(
        "--out",
        help="Report path. Default: <deliverable stem>.l1.json next to the deliverable. "
        "The file name must end with .l1.json (L1-07).",
    )
    return parser


def _usage_error(parser: argparse.ArgumentParser):
    def error(message: str) -> None:
        parser.print_usage(sys.stderr)
        raise UsageError(message)

    return error
