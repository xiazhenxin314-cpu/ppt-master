#!/usr/bin/env python3
"""Report assembly for page checks.

Pure: `check_pages` takes parsed pages, never reads or writes a file, and never
prints. It collects findings, keeps a per-page failure envelope, and ranks pages
by how many findings they carry. Nothing in the report is a score.

Usage:
    Imported by page_checks.cli and by tests.

Examples:
    report = check_pages([parse_page(path)], select_checks(), {})

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

from typing import Sequence

from .checks import CHECKS, Check, CheckResult
from .page import Page

SCHEMA_ID = "ppt-master.page-check.v1"
CATCHABLE_CHECK_ERRORS = (ValueError, TypeError, KeyError, ArithmeticError)


def check_pages(
    pages: Sequence[Page],
    checks: Sequence[Check] | None = None,
    config: dict | None = None,
) -> dict:
    """Collect findings for every page and rank pages by finding count."""
    selected = list(checks) if checks is not None else list(CHECKS.values())
    resolved = dict(config or {})
    report: dict = {
        "schema": SCHEMA_ID,
        "checks": [spec.id for spec in selected],
        "pages": [],
    }
    for page in pages:
        if page.error:
            report["pages"].append({"page": page.name, "error": page.error, "findings": []})
            continue
        report["pages"].append(_check_page(page, selected, resolved))
    report["pages"].sort(key=_rank_key)
    return report


def _rank_key(row: dict) -> tuple[int, int]:
    """Unreadable pages first, then pages with a failing check, then most findings."""
    if "error" in row:
        return (0, 0)
    if row.get("errors"):
        return (1, 0)
    return (2, -len(row.get("findings", [])))


def _check_page(page: Page, selected: Sequence[Check], config: dict) -> dict:
    """Run every selected check, recording a broken check instead of crashing."""
    findings: list[dict] = []
    errors: list[dict] = []
    details: dict[str, dict] = {}
    for spec in selected:
        result = _run_check(spec, page, config, errors)
        for finding in result.findings:
            findings.append({**finding, "check": spec.id})
        if result.detail:
            details[spec.id] = result.detail
    row: dict = {
        "page": page.name,
        "findings": findings,
        "canvas": [page.width, page.height],
    }
    if errors:
        row["errors"] = errors
    if details:
        row["detail"] = details
    return row


def _run_check(spec: Check, page: Page, config: dict, errors: list[dict]) -> CheckResult:
    """Run one check; a declared failure becomes a recorded error, not a finding."""
    try:
        result = spec.func(page, config)
    except CATCHABLE_CHECK_ERRORS as exc:
        errors.append({"check": spec.id, "error": str(exc)})
        return CheckResult()
    if not isinstance(result, CheckResult):
        raise TypeError(
            f"check {spec.id!r} returned {type(result).__name__}; "
            "a check must return CheckResult(findings=[...], detail={...})"
        )
    return result
