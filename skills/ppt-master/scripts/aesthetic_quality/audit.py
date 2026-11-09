#!/usr/bin/env python3
"""Report assembly for the aesthetic audit.

Pure: `audit_pages` takes parsed pages, never reads or writes a file, and never
prints. Ranking, scoring, and the page-level error envelope live here.

Usage:
    Imported by aesthetic_quality.cli and by tests.

Examples:
    report = audit_pages([parse_page(path)], select_metrics(), {"grid": 8})

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

from typing import Sequence

from .metrics import METRICS, Metric, MetricResult, clamp_score
from .page import Page

SCHEMA_ID = "ppt-master.aesthetic-audit.v1"
CATCHABLE_METRIC_ERRORS = (ValueError, TypeError, KeyError, ArithmeticError)


def audit_pages(
    pages: Sequence[Page],
    metrics: Sequence[Metric] | None = None,
    config: dict | None = None,
) -> dict:
    """Score every page with every selected metric and rank pages worst-first.

    A page whose metric failed is ranked before every scored page and carries
    `status: "error"`, so a broken metric can never look like a clean zero.
    """
    selected = list(metrics) if metrics is not None else list(METRICS.values())
    resolved = dict(config or {})
    report = {
        "schema": SCHEMA_ID,
        "metrics": [spec.id for spec in selected],
        "pages": [],
    }
    for page in pages:
        if page.error:
            report["pages"].append(
                {
                    "page": page.name,
                    "score": 1.0,
                    "status": "error",
                    "error": page.error,
                }
            )
            continue
        report["pages"].append(_score_page(page, selected, resolved))
    report["pages"].sort(key=_rank_key)
    return report


def _rank_key(row: dict) -> tuple[int, float]:
    """Error rows first, then worst score first."""
    return (0 if row.get("status") == "error" else 1, -float(row.get("score", 0.0)))


def _score_page(page: Page, selected: Sequence[Metric], config: dict) -> dict:
    """Score one page; a failing metric records a page-level error, never a crash."""
    scores: dict[str, float] = {}
    findings: list[dict] = []
    errors: list[dict] = []
    for spec in selected:
        result = _run_metric(spec, page, config, errors)
        scores[spec.id] = round(result.score, 4)
        for finding in result.findings:
            findings.append({**finding, "metric": spec.id})
    row = {
        "page": page.name,
        "score": round(max(scores.values()) if scores else 0.0, 4),
        "scores": scores,
        "findings": findings,
        "canvas": [page.width, page.height],
    }
    if errors:
        row["errors"] = errors
        row["status"] = "error"
    return row


def _run_metric(spec: Metric, page: Page, config: dict, errors: list[dict]) -> MetricResult:
    """Run one metric; a declared failure becomes a recorded error.

    A metric that returns something other than `MetricResult` is a broken
    extension, not a page defect, so it raises instead of scoring zero.
    """
    try:
        result = spec.func(page, config)
    except CATCHABLE_METRIC_ERRORS as exc:
        errors.append({"metric": spec.id, "error": str(exc)})
        return MetricResult(score=0.0)
    if not isinstance(result, MetricResult):
        raise TypeError(
            f"metric {spec.id!r} returned {type(result).__name__}; "
            "a metric must return MetricResult(score=..., findings=[...], detail={...})"
        )
    try:
        score = clamp_score(result.score)
    except CATCHABLE_METRIC_ERRORS as exc:
        errors.append({"metric": spec.id, "error": str(exc)})
        return MetricResult(score=0.0)
    return MetricResult(score=score, findings=result.findings, detail=result.detail)
