#!/usr/bin/env python3
"""Factual checks over one page.

A check reports findings, never scores. Each finding states a fact about the
page -- "this text is 4.48:1 on the flat block behind it", "this box extends
12px past the canvas" -- and never an opinion about the design. A deliberate
low-contrast cover or an intentional bleed is a fact too, which is why the
report lists it without judging it.

Usage:
    Imported by page_checks.report and page_checks.cli.

Examples:
    @check("off_canvas", "element extends past the canvas edge")
    def _off_canvas(page, config): ...

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .page import Page, contrast_ratio, parse_color


@dataclass
class CheckResult:
    """One check's findings for one page, plus what it had to skip."""

    findings: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class Check:
    """One registered check; `func` maps (page, config) to CheckResult."""

    id: str
    summary: str
    func: Callable[[Page, dict], CheckResult]


CHECKS: dict[str, Check] = {}


def check(check_id: str, summary: str):
    """Register one check."""

    def register(func: Callable[[Page, dict], CheckResult]) -> Callable[[Page, dict], CheckResult]:
        CHECKS[check_id] = Check(id=check_id, summary=summary, func=func)
        return func

    return register


def select_checks(only: set[str] | None = None) -> list[Check]:
    """Return the registered checks, optionally restricted to `only` ids."""
    return [spec for key, spec in CHECKS.items() if not only or key in only]


def _hex(rgb) -> str:
    """Render a parsed colour as #RRGGBB for a finding message."""
    return "#{:02X}{:02X}{:02X}".format(*(int(round(value * 255)) for value in rgb[:3]))


def _contains(outer, inner, slack: float = 1.0) -> bool:
    """Return whether `outer` covers `inner` within a one-pixel slack."""
    return (
        outer[0] <= inner[0] + slack
        and outer[1] <= inner[1] + slack
        and outer[2] >= inner[2] - slack
        and outer[3] >= inner[3] - slack
    )


def _background_under(page: Page, node) -> tuple[tuple[float, float, float, float] | None, str]:
    """Resolve the paint directly behind one text box.

    Returns (colour, reason). `colour` is None when the background cannot be
    known from the page: a photograph, a gradient, an unsupported paint, or a
    translucent layer sits under the text. Callers skip those runs instead of
    inventing a background and reporting a false contrast failure.
    """
    target = node.bbox
    # Later nodes paint over earlier ones, so scan top-down; the first opaque
    # cover wins, and the page's own full-canvas rectangle ends the scan.
    for candidate in reversed(page.nodes):
        if candidate is node or candidate.tag == "text":
            continue
        if not _contains(candidate.bbox, target):
            continue
        if candidate.tag == "image":
            return None, "image"
        paint = (candidate.fill or "").strip()
        if not paint or paint == "none":
            continue  # transparent: what is under it still shows through
        if paint.startswith("url("):
            return None, "gradient-or-pattern"
        colour = parse_color(paint)
        if colour is None:
            return None, "unresolved-paint"
        if colour[3] < 1.0:
            return None, "translucent-layer"
        return colour, "flat"
    fallback = parse_color(page.background())
    if fallback is None:
        return None, "no-flat-page-background"
    return fallback, "page-background"


def _is_large_text(node) -> bool:
    """Return whether WCAG treats this run as large text."""
    weight = str(node.attrs.get("font-weight", "400")).strip().lower()
    bold = weight in {"bold", "bolder", "600", "700", "800", "900"}
    return node.font_size >= 24 or (bold and node.font_size >= 18.66)


@check("contrast", "text below the WCAG floor on a background the page can resolve")
def contrast_check(page: Page, config: dict) -> CheckResult:
    """Report text runs whose known background leaves them below the floor."""
    minimum = float(config.get("min_contrast", 4.5))
    minimum_large = float(config.get("min_contrast_large", 3.0))
    findings: list[dict] = []
    skipped: dict[str, int] = {}
    for node in page.text_nodes():
        foreground = parse_color(node.fill)
        if foreground is None:
            skipped["text-paint"] = skipped.get("text-paint", 0) + 1
            continue
        background, reason = _background_under(page, node)
        if background is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        required = minimum_large if _is_large_text(node) else minimum
        ratio = contrast_ratio(foreground, background)
        if ratio < required:
            findings.append(
                {
                    "id": node.id,
                    "message": (
                        f"text is {ratio:.2f}:1 on {_hex(background)}, "
                        f"below {required:g}:1"
                    ),
                    "text": node.text[:30],
                    "ratio": round(ratio, 2),
                    "required": required,
                    "background": _hex(background),
                }
            )
    return CheckResult(
        findings=findings,
        detail={
            "min_contrast": minimum,
            "min_contrast_large": minimum_large,
            "skipped": skipped,
        },
    )


@check("off_canvas", "an element extends past the canvas edge")
def off_canvas_check(page: Page, config: dict) -> CheckResult:
    """Report elements whose bounding box leaves the canvas by more than a pixel."""
    tolerance = float(config.get("tolerance", 1.0))
    width, height = page.width, page.height
    findings: list[dict] = []
    checked = 0
    for node in page.shapes():
        checked += 1
        x0, y0, x1, y1 = node.bbox
        overflow = max(0.0, -x0, -y0, x1 - width, y1 - height)
        if overflow > tolerance:
            findings.append(
                {
                    "id": node.id,
                    "message": f"extends {overflow:.1f}px past the canvas edge",
                    "tag": node.tag,
                    "overflow_px": round(overflow, 1),
                }
            )
    return CheckResult(
        findings=findings,
        detail={"tolerance": tolerance, "checked": checked},
    )
