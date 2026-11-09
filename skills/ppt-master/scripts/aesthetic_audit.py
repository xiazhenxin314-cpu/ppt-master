#!/usr/bin/env python3
"""PPT Master - Aesthetic Audit

Stable CLI entry point and compatibility import surface for the per-page
aesthetic audit. Implementation lives in `aesthetic_quality/`.

Usage:
    python3 scripts/aesthetic_audit.py <project_path>
    python3 scripts/aesthetic_audit.py <dir_or_svg> --json out.json
    python3 scripts/aesthetic_audit.py <project_path> --only contrast,grid_deviation

Examples:
    python3 scripts/aesthetic_audit.py projects/demo --json validation/aesthetic_audit.json
    python3 scripts/aesthetic_audit.py projects/demo --grid 8 --min-contrast 4.5 --top 5

Dependencies:
    Same as aesthetic_quality.cli.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

from aesthetic_quality import (  # noqa: E402
    Metric,
    MetricResult,
    Page,
    SCHEMA_ID,
    audit_pages,
    metric,
    parse_page,
)
from aesthetic_quality.cli import main  # noqa: E402

__all__ = [
    "Metric",
    "MetricResult",
    "Page",
    "SCHEMA_ID",
    "audit_pages",
    "main",
    "metric",
    "parse_page",
]


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
