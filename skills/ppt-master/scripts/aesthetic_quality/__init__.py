"""Per-page aesthetic audit: page model, metrics, report, and CLI."""

from .audit import SCHEMA_ID, audit_pages
from .metrics import Metric, MetricResult, metric
from .page import Page, parse_page

__all__ = [
    "Metric",
    "MetricResult",
    "Page",
    "SCHEMA_ID",
    "audit_pages",
    "metric",
    "parse_page",
]
