#!/usr/bin/env python3
"""Command line for page checks.

Owns argument parsing, page discovery, terminal rendering, and the optional JSON
report. A page that cannot be parsed keeps one row in the report so nothing is
silently dropped.

Usage:
    python3 scripts/page_check.py <project_path> [options]

Examples:
    python3 scripts/page_check.py projects/demo
    python3 scripts/page_check.py projects/demo --json validation/page_check.json
    python3 scripts/page_check.py projects/demo --only off_canvas --top 5

Dependencies:
    Standard library plus local PPT Master sibling modules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from slide_roster import discover_slide_svgs

from .checks import CHECKS, select_checks
from .page import Page, parse_page
from .report import check_pages


def collect_pages(target: Path) -> list[Path]:
    """Resolve the page roster for a project directory, svg directory, or file."""
    if target.is_file():
        return [target]
    output = target / "svg_output"
    root = output if output.is_dir() else target
    return discover_slide_svgs(root)


def parse_pages(paths: list[Path]) -> list[Page]:
    """Parse every page, folding a parse failure into an error-bearing Page."""
    pages: list[Page] = []
    for path in paths:
        try:
            pages.append(parse_page(path))
        except ET.ParseError as exc:
            pages.append(Page(path=path, width=0.0, height=0.0, error=f"parse: {exc}"))
    return pages


def render_table(report: dict, top: int = 10) -> str:
    """Render the human-readable findings table."""
    lines = [f'{"page":<28}{"findings":>9}  first finding']
    for row in report["pages"][:top]:
        if "error" in row:
            lines.append(f'{row["page"]:<28}{"ERR":>9}  {row["error"]}')
            continue
        findings = row.get("findings", [])
        if not findings:
            lines.append(f'{row["page"]:<28}{0:>9}  -')
            continue
        first = findings[0]
        lines.append(f'{row["page"]:<28}{len(findings):>9}  {first.get("check")}: {first.get("message", "")}')
    lines.append(f'pages={len(report["pages"])}  checks={",".join(report["checks"])}')
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Report factual page problems: no scores, only findings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("target", nargs="?", help="project path, svg directory, or one svg file")
    parser.add_argument("--json", metavar="PATH", help="write the full report here")
    parser.add_argument("--only", metavar="IDS", help="comma separated check ids")
    parser.add_argument(
        "--min-contrast",
        type=float,
        default=4.5,
        help="WCAG floor for body text (default 4.5)",
    )
    parser.add_argument("--list", action="store_true", help="list registered checks and exit")
    parser.add_argument("--top", type=int, default=10, help="rows printed to stdout (default 10)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the checks and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        for key, spec in CHECKS.items():
            print(f"{key:<14} {spec.summary}")
        return 0
    if not args.target:
        parser.print_help()
        return 2
    target = Path(args.target).resolve()
    paths = collect_pages(target)
    if not paths:
        print(f"No SVG pages found under {target}", file=sys.stderr)
        return 2
    only = {part.strip() for part in args.only.split(",")} if args.only else None
    selected = select_checks(only)
    if not selected:
        print("No registered check matches --only", file=sys.stderr)
        return 2
    report = check_pages(parse_pages(paths), selected, {"min_contrast": args.min_contrast})
    if args.json:
        destination = Path(args.json)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            destination.write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(f"cannot write report to {destination}: {exc}", file=sys.stderr)
            return 2
        print(f"report: {destination}", file=sys.stderr)
    print(render_table(report, args.top))
    return 0
