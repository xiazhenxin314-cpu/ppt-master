#!/usr/bin/env python3
"""Per-page factual checks: page model, check registry, report, and CLI.

A check reports findings, never a score: the report says what contradicts what,
and leaves every judgement about the design to the reader.

Usage:
    Imported by page_check.py and by tests.

Examples:
    from page_checks import check_pages, parse_page, select_checks

Dependencies:
    None (only uses standard library)
"""

from .checks import CHECKS, Check, CheckResult, check, select_checks
from .page import Node, Page, contrast_ratio, parse_color, parse_page
from .report import SCHEMA_ID, check_pages

__all__ = [
    "Check",
    "CheckResult",
    "Page",
    "SCHEMA_ID",
    "check",
    "check_pages",
    "parse_page",
    "select_checks",
]
