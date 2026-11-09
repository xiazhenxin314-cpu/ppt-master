#!/usr/bin/env python3
"""Registered aesthetic metrics.

A metric is one function that scores one page from 0.0 (clean) to 1.0 (worst)
against a number the run already declares -- never against taste. Registering a
metric is the only extension point; the driver never changes.

Usage:
    Imported by aesthetic_quality.audit and aesthetic_quality.cli.

Examples:
    @metric("whitespace_ratio", "Share of the canvas left empty")
    def _whitespace(page, config): ...

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .page import Page, contrast_ratio, parse_color


@dataclass
class MetricResult:
    """One metric's verdict for one page."""

    score: float = 0.0
    findings: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class Metric:
    """One registered metric; `func` maps (page, config) to MetricResult."""

    id: str
    summary: str
    func: Callable[[Page, dict], MetricResult]


METRICS: dict[str, Metric] = {}


def metric(metric_id: str, summary: str):
    """Register one audit metric."""

    def register(func: Callable[[Page, dict], MetricResult]) -> Callable[[Page, dict], MetricResult]:
        METRICS[metric_id] = Metric(id=metric_id, summary=summary, func=func)
        return func

    return register


def select_metrics(only: set[str] | None = None) -> list[Metric]:
    """Return the registered metrics, optionally restricted to `only` ids."""
    return [spec for key, spec in METRICS.items() if not only or key in only]


def clamp_score(value: float) -> float:
    """Clamp a raw score into 0.0..1.0; NaN is a metric failure, never "clean"."""
    score = float(value)
    if score != score:
        raise ValueError("metric returned a NaN score")
    return max(0.0, min(1.0, score))


@metric("contrast", "WCAG contrast of text against its page background")
def contrast_metric(page: Page, config: dict) -> MetricResult:
    """Flag text runs below the declared contrast floor."""
    minimum = float(config.get("min_contrast", 4.5))
    minimum_large = float(config.get("min_contrast_large", 3.0))
    background = parse_color(page.background())
    if background is None:
        return MetricResult(detail={"min_contrast": minimum, "skipped": "no flat page background"})
    findings: list[dict] = []
    worst = 0.0
    for node in page.text_nodes():
        foreground = parse_color(node.fill)
        if foreground is None:
            continue
        # WCAG relaxes the floor for large text: 18.66px bold or 24px regular.
        required = minimum_large if _is_large_text(node) else minimum
        ratio = contrast_ratio(foreground, background)
        if ratio < required:
            worst = max(worst, (required - ratio) / required)
            findings.append(
                {
                    "id": node.id,
                    "text": node.text[:30],
                    "ratio": round(ratio, 2),
                    "required": required,
                }
            )
    return MetricResult(
        score=clamp_score(worst),
        findings=findings,
        detail={"min_contrast": minimum, "min_contrast_large": minimum_large},
    )


def _is_large_text(node) -> bool:
    """Return whether WCAG treats this run as large text."""
    weight = str(node.attrs.get("font-weight", "400")).strip().lower()
    bold = weight in {"bold", "bolder", "600", "700", "800", "900"}
    return node.font_size >= 24 or (bold and node.font_size >= 18.66)


@metric("grid_deviation", "Distance of shape edges from the declared layout grid")
def grid_deviation_metric(page: Page, config: dict) -> MetricResult:
    """Flag edges that sit off the authored grid by more than the tolerance."""
    step = float(config.get("grid") or 0)
    if step <= 0:
        # A grid is a project decision, not a universal constant: the bundled
        # layouts use a 4px rhythm for scrims and an 8px one for text bounds.
        return MetricResult(detail={"skipped": "no declared grid; pass --grid"})
    tolerance = float(config.get("tolerance", 1.0))
    findings: list[dict] = []
    worst = 0.0
    checked = 0
    for node in page.shapes({"rect", "image", "text"}):
        checked += 1
        # Only the authored anchor edges are grid obligations. A width or height
        # is free to land anywhere, and a text box is an estimate, so text
        # contributes its x anchor alone -- its baseline is typography, not grid.
        offsets = []
        for value in node.bbox[:1] if node.tag == "text" else node.bbox[:2]:
            remainder = abs(value - round(value / step) * step)
            offsets.append(min(remainder, step - remainder))
        deviation = max(offsets)
        if deviation > tolerance:
            worst = max(worst, min(deviation / (step / 2), 1.0))
            findings.append({"id": node.id, "tag": node.tag, "deviation": round(deviation, 2)})
    return MetricResult(
        score=clamp_score(worst),
        findings=findings,
        detail={"grid": step, "checked": checked},
    )
