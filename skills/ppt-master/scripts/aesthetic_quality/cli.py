#!/usr/bin/env python3
"""Command line for the aesthetic audit.

Owns argument parsing, page discovery, terminal rendering, and the optional JSON
report. Parsed-page failures are wrapped into a Page with `error` set so the
report keeps one row per discovered page.

Usage:
    python3 scripts/aesthetic_audit.py <project_path> [options]

Examples:
    python3 scripts/aesthetic_audit.py projects/demo --json validation/aesthetic_audit.json
    python3 scripts/aesthetic_audit.py projects/demo --only contrast --top 5

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

from .audit import audit_pages
from .metrics import METRICS, select_metrics
from .page import Page, parse_page


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
    """Render the human-readable ranking table."""
    lines = [f'{"page":<28}{"score":>7}  worst metric']
    for row in report["pages"][:top]:
        if row.get("status") == "error":
            reason = row.get("error") or "; ".join(
                entry.get("error", "") for entry in row.get("errors", [])
            ) or "metric failed"
            lines.append(f'{row["page"]:<28}{"ERR":>7}  {reason}')
            continue
        scores = row.get("scores") or {}
        worst = max(scores, key=scores.get) if scores else "-"
        lines.append(f'{row["page"]:<28}{row["score"]:>7.3f}  {worst}={scores.get(worst, 0):.3f}')
    lines.append(f'pages={len(report["pages"])}  metrics={",".join(report["metrics"])}')
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Per-page proxy metrics for aesthetic repair.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("target", nargs="?", help="project path, svg directory, or one svg file")
    parser.add_argument("--json", metavar="PATH", help="write the full report here")
    parser.add_argument("--only", metavar="IDS", help="comma separated metric ids")
    parser.add_argument(
        "--grid",
        type=float,
        default=None,
        help="layout grid step in px; omit to skip the grid metric",
    )
    parser.add_argument(
        "--min-contrast",
        type=float,
        default=4.5,
        help="WCAG threshold (default 4.5)",
    )
    parser.add_argument("--list", action="store_true", help="list registered metrics and exit")
    parser.add_argument("--top", type=int, default=10, help="rows printed to stdout (default 10)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the audit and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        for key, spec in METRICS.items():
            print(f"{key:<18} {spec.summary}")
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
    selected = select_metrics(only)
    if not selected:
        print("No registered metric matches --only", file=sys.stderr)
        return 2
    config = {"grid": args.grid, "min_contrast": args.min_contrast}
    report = audit_pages(parse_pages(paths), selected, config)
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
