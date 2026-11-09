#!/usr/bin/env python3
"""Findings, ordering, and skipped-background regressions for page checks."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from page_checks import (  # noqa: E402
    Check,
    CheckResult,
    check_pages,
    parse_page,
    select_checks,
)
from page_checks.cli import main  # noqa: E402

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <rect id="card" x="100" y="100" width="400" height="200" fill="#000000"/>
  <text id="on-card" x="120" y="160" font-size="20" fill="#FFFFFF">ok</text>
  <text id="on-page" x="120" y="400" font-size="20" fill="#EEEEEE">low</text>
</svg>
"""

ON_IMAGE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <image id="photo" x="0" y="0" width="600" height="300" href="photo.png"/>
  <text id="on-photo" x="20" y="100" font-size="20" fill="#FFFFFF">caption</text>
</svg>
"""

ON_GRADIENT = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <rect id="band" x="0" y="200" width="1280" height="200" fill="url(#g)"/>
  <text id="on-band" x="40" y="300" font-size="20" fill="#FFFFFF">caption</text>
</svg>
"""

OFF_CANVAS = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <rect id="bleed" x="1200" y="100" width="200" height="100" fill="#000000"/>
  <rect id="inside" x="10" y="10" width="100" height="100" fill="#000000"/>
</svg>
"""


def write(tmp: str, name: str, body: str) -> Path:
    """Write one fixture page."""
    path = Path(tmp) / name
    path.write_text(body, encoding="utf-8")
    return path


class ReportShapeTest(unittest.TestCase):
    """The report carries findings and counts, never a score."""

    def test_no_score_anywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", PAGE))
        report = check_pages([page], select_checks({"contrast"}), {})
        self.assertEqual(report["schema"], "ppt-master.page-check.v1")
        self.assertNotIn("score", json.dumps(report))
        self.assertNotIn("scores", json.dumps(report))
        self.assertEqual(report["checks"], ["contrast"])

    def test_pages_ordered_by_finding_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            quiet = parse_page(write(tmp, "P01.svg", PAGE.replace("#EEEEEE", "#111111")))
            loud = parse_page(write(tmp, "P02.svg", PAGE))
        report = check_pages([quiet, loud], select_checks({"contrast"}), {})
        self.assertEqual([row["page"] for row in report["pages"]], ["P02", "P01"])

    def test_unreadable_page_keeps_a_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "P01.svg", "<svg><rect")
            try:
                parse_page(path)
            except Exception:
                from page_checks.page import Page

                page = Page(path=path, width=0.0, height=0.0, error="parse: boom")
            report = check_pages([page], select_checks({"contrast"}), {})
        self.assertEqual(report["pages"][0]["findings"], [])
        self.assertIn("error", report["pages"][0])

    def test_broken_check_is_reported_not_raised(self) -> None:
        def broken(page, config):
            raise ValueError("boom")

        spec = Check(id="broken", summary="always fails", func=broken)
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", PAGE))
        report = check_pages([page], [spec], {})
        self.assertEqual(report["pages"][0]["errors"][0]["check"], "broken")


class ContrastTest(unittest.TestCase):
    """Contrast uses the flat paint actually behind the text, or stays quiet."""

    def test_text_on_flat_card_uses_the_card_not_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", PAGE))
        report = check_pages([page], select_checks({"contrast"}), {})
        ids = [finding["id"] for finding in report["pages"][0]["findings"]]
        self.assertEqual(ids, ["on-page"])
        finding = report["pages"][0]["findings"][0]
        self.assertEqual(finding["check"], "contrast")
        self.assertLess(finding["ratio"], 4.5)

    def test_text_over_an_image_is_skipped_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", ON_IMAGE))
        report = check_pages([page], select_checks({"contrast"}), {})
        self.assertEqual(report["pages"][0]["findings"], [])
        self.assertEqual(report["pages"][0]["detail"]["contrast"]["skipped"].get("image"), 1)

    def test_text_over_a_gradient_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", ON_GRADIENT))
        report = check_pages([page], select_checks({"contrast"}), {})
        self.assertEqual(report["pages"][0]["findings"], [])
        skipped = report["pages"][0]["detail"]["contrast"]["skipped"]
        self.assertEqual(skipped.get("gradient-or-pattern"), 1)

    def test_large_text_uses_the_relaxed_floor(self) -> None:
        body = PAGE.replace('font-size="20" fill="#EEEEEE"', 'font-size="30" fill="#999999"')
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", body))
        report = check_pages([page], select_checks({"contrast"}), {})
        finding = [row for row in report["pages"][0]["findings"] if row["id"] == "on-page"][0]
        self.assertEqual(finding["required"], 3.0)


class OffCanvasTest(unittest.TestCase):
    """Elements past the canvas edge are a fact, not an opinion."""

    def test_flags_only_the_overflowing_element(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", OFF_CANVAS))
        report = check_pages([page], select_checks({"off_canvas"}), {})
        ids = [finding["id"] for finding in report["pages"][0]["findings"]]
        self.assertEqual(ids, ["bleed"])

    def test_one_pixel_of_slack_is_tolerated(self) -> None:
        body = OFF_CANVAS.replace('x="1200" y="100" width="200"', 'x="1179" y="100" width="102"')
        with tempfile.TemporaryDirectory() as tmp:
            page = parse_page(write(tmp, "P01.svg", body))
        report = check_pages([page], select_checks({"off_canvas"}), {})
        self.assertEqual(report["pages"][0]["findings"], [])


class CliTest(unittest.TestCase):
    """Documented exit codes and flags."""

    def test_unknown_only_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "P01.svg", PAGE)
            with contextlib.redirect_stderr(io.StringIO()):
                code = main([tmp, "--only", "nope"])
        self.assertEqual(code, 2)

    def test_missing_target_exits_two(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 2)

    def test_list_exits_zero_and_prints_ids(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(main(["--list"]), 0)
        self.assertIn("contrast", buffer.getvalue())
        self.assertIn("off_canvas", buffer.getvalue())

    def test_json_report_has_no_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = write(tmp, "P01.svg", PAGE)
            out = Path(tmp) / "validation" / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(page), "--json", str(out)]), 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertNotIn("score", json.dumps(payload))
        self.assertEqual(payload["pages"][0]["findings"][0]["check"], "contrast")


if __name__ == "__main__":
    unittest.main()
