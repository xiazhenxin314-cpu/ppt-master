#!/usr/bin/env python3
"""
PPT Master - Page Check

Reports factual problems on each page SVG: text below the contrast floor on a
background the page can resolve, and elements that extend past the canvas.
Findings only -- the tool never scores a page and never judges the design.

Usage:
    python3 scripts/page_check.py <project_path> [options]

Examples:
    python3 scripts/page_check.py projects/demo
    python3 scripts/page_check.py projects/demo --json validation/page_check.json
    python3 scripts/page_check.py projects/demo --only off_canvas --top 5

Dependencies:
    Standard library plus sibling modules: slide_roster, console_encoding,
    attribution_guard, and the page_checks package.

See scripts/docs/page-check.md for the report schema and check contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402
from page_checks import CHECKS, Check, CheckResult, Page, SCHEMA_ID, check, check_pages, parse_page
from page_checks.cli import main  # noqa: E402

configure_utf8_stdio()

__all__ = [
    "CHECKS",
    "Check",
    "CheckResult",
    "Page",
    "SCHEMA_ID",
    "check",
    "check_pages",
    "main",
    "parse_page",
]

if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
