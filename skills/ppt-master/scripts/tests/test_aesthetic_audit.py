#!/usr/bin/env python3
"""Regressions for the per-page aesthetic audit."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import aesthetic_quality  # noqa: E402
from aesthetic_quality import (  # noqa: E402
    SCHEMA_ID,
    Metric,
    MetricResult,
    Page,
    audit_pages,
    parse_page,
)
from aesthetic_quality.cli import main  # noqa: E402
from aesthetic_quality.metrics import select_metrics  # noqa: E402
from aesthetic_quality.page import parse_color  # noqa: E402

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <rect id="off-grid" x="13" y="64" width="200" height="104" fill="#EEEEEE"/>
  <text id="low" x="16" y="120" font-size="20" fill="#999999">low contrast</text>
</svg>
"""

LARGE_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <text id="big" x="16" y="120" font-size="30" fill="#888888">large</text>
  <text id="small" x="16" y="240" font-size="16" fill="#888888">small</text>
</svg>
"""

CLEAN = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <text id="ok" x="16" y="120" font-size="20" fill="#000000">ok</text>
</svg>
"""


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run main() with captured streams."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class PageModelTest(unittest.TestCase):
    """Bounding boxes and inherited paint."""

    def test_rect_bbox_and_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "P01.svg"
            path.write_text(PAGE, encoding="utf-8")
            page = parse_page(path)
        self.assertEqual((page.width, page.height), (1280, 720))
        self.assertEqual(page.background(), "#FFFFFF")
        rect = next(node for node in page.shapes() if node.id == "off-grid")
        self.assertEqual(rect.bbox, (13.0, 64.0, 213.0, 168.0))

    def test_text_nodes_only_visible_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "P01.svg"
            path.write_text(PAGE, encoding="utf-8")
            page = parse_page(path)
        self.assertEqual([node.id for node in page.text_nodes()], ["low"])


class AuditReportTest(unittest.TestCase):
    """Scoring, ranking, filtering, and error envelopes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "P01.svg").write_text(PAGE, encoding="utf-8")
        (self.root / "P02.svg").write_text(CLEAN, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scores_are_bounded_and_reported(self) -> None:
        pages = [parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, select_metrics(), {"grid": 8})
        self.assertEqual(report["schema"], SCHEMA_ID)
        self.assertEqual(sorted(report["metrics"]), ["contrast", "grid_deviation"])
        scores = report["pages"][0]["scores"]
        self.assertGreater(scores["contrast"], 0.0)
        self.assertLess(scores["contrast"], 0.5)
        self.assertGreater(scores["grid_deviation"], 0.0)
        self.assertEqual(
            report["pages"][0]["score"],
            max(report["pages"][0]["scores"].values()),
        )

    def test_findings_name_the_offending_node(self) -> None:
        pages = [parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, select_metrics(), {"grid": 8})
        ids = {finding.get("id") for finding in report["pages"][0]["findings"]}
        self.assertIn("low", ids)
        self.assertIn("off-grid", ids)

    def test_clean_page_scores_zero(self) -> None:
        pages = [parse_page(self.root / "P02.svg")]
        report = audit_pages(pages, select_metrics(), {})
        self.assertEqual(report["pages"][0]["score"], 0.0)

    def test_only_restricts_metrics(self) -> None:
        selected = select_metrics({"contrast"})
        self.assertEqual([spec.id for spec in selected], ["contrast"])
        pages = [parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, selected, {"grid": 8})
        self.assertEqual(report["metrics"], ["contrast"])
        self.assertEqual(sorted(report["pages"][0]["scores"]), ["contrast"])

    def test_pages_rank_worst_first(self) -> None:
        pages = [parse_page(self.root / "P02.svg"), parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, select_metrics(), {"grid": 8})
        self.assertEqual([row["page"] for row in report["pages"]], ["P01", "P02"])

    def test_grid_metric_skips_without_a_declared_grid(self) -> None:
        pages = [parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, select_metrics(), {})
        self.assertEqual(report["pages"][0]["scores"]["grid_deviation"], 0.0)
        self.assertNotIn(
            "off-grid",
            {finding.get("id") for finding in report["pages"][0]["findings"]},
        )

    def test_large_text_uses_the_relaxed_floor(self) -> None:
        (self.root / "P03.svg").write_text(LARGE_TEXT, encoding="utf-8")
        pages = [parse_page(self.root / "P03.svg")]
        report = audit_pages(pages, select_metrics({"contrast"}), {})
        ids = {finding.get("id") for finding in report["pages"][0]["findings"]}
        self.assertIn("small", ids)
        self.assertNotIn("big", ids)

    def test_metric_failure_is_recorded_not_raised(self) -> None:
        def explode(page: Page, config: dict) -> None:
            raise ValueError("boom")

        broken = Metric(id="boom", summary="always fails", func=explode)
        pages = [parse_page(self.root / "P02.svg")]
        report = audit_pages(pages, [broken], {})
        row = report["pages"][0]
        self.assertEqual(row["scores"]["boom"], 0.0)
        self.assertEqual(row["errors"], [{"metric": "boom", "error": "boom"}])

    def test_parse_error_page_keeps_one_row(self) -> None:
        broken = Page(path=Path("bad.svg"), width=0.0, height=0.0, error="parse: unexpected end")
        report = audit_pages([broken], select_metrics(), {})
        self.assertEqual(report["pages"][0]["page"], "bad")
        self.assertIn("error", report["pages"][0])


class CliTest(unittest.TestCase):
    """Exit codes, report writing, and stream separation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_json_report_written_and_path_on_stderr(self) -> None:
        (self.root / "svg_output").mkdir()
        (self.root / "svg_output" / "P01.svg").write_text(PAGE, encoding="utf-8")
        destination = self.root / "validation" / "audit.json"
        code, stdout, stderr = run_cli([str(self.root), "--json", str(destination)])
        self.assertEqual(code, 0)
        self.assertTrue(destination.is_file())
        self.assertIn("report:", stderr)
        self.assertIn("P01", stdout)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], SCHEMA_ID)

    def test_empty_directory_exits_two(self) -> None:
        code, stdout, stderr = run_cli([str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("No SVG pages", stderr)

    def test_unparseable_page_becomes_error_row(self) -> None:
        (self.root / "P01.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><', encoding="utf-8")
        code, stdout, stderr = run_cli([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("ERR", stdout)

    def test_list_prints_registered_metrics(self) -> None:
        code, stdout, _ = run_cli(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("contrast", stdout)
        self.assertIn("grid_deviation", stdout)


ROTATED = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
  <rect id="spun" x="0" y="0" width="10" height="10" fill="#111111"
        transform="rotate(90,50,50)"/>
</svg>
"""

LARGE_NEAR_MISS = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <text id="big" x="16" y="120" font-size="30" fill="#999999">large</text>
</svg>
"""


class InterfaceTest(unittest.TestCase):
    """The public surface is exactly the documented seven names."""

    def test_package_all_is_the_seven_names(self) -> None:
        self.assertEqual(
            sorted(aesthetic_quality.__all__),
            [
                "Metric",
                "MetricResult",
                "Page",
                "SCHEMA_ID",
                "audit_pages",
                "metric",
                "parse_page",
            ],
        )

    def test_shim_reexports_the_same_set_plus_main(self) -> None:
        import aesthetic_audit as shim

        self.assertEqual(
            set(shim.__all__) - {"main"},
            set(aesthetic_quality.__all__),
        )


class GeometryTest(unittest.TestCase):
    """Transforms compose the way SVG says they do."""

    def test_rotate_uses_its_centre(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "P01.svg"
            path.write_text(ROTATED, encoding="utf-8")
            page = parse_page(path)
        node = next(node for node in page.shapes() if node.id == "spun")
        # rotate(90,50,50) maps (0,0)->(100,0) and (10,10)->(90,10).
        for actual, expected in zip(node.bbox, (90.0, 0.0, 100.0, 10.0)):
            self.assertAlmostEqual(actual, expected, delta=1.0)


class MetricContractTest(unittest.TestCase):
    """Values the driver must reject or record, not accept silently."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "P01.svg").write_text(CLEAN, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_metric_returning_a_dict_is_rejected(self) -> None:
        def wrong(page: Page, config: dict):
            return {"score": 0.5}

        broken = Metric(id="dict_metric", summary="returns dict", func=wrong)
        pages = [parse_page(self.root / "P01.svg")]
        with self.assertRaises(TypeError) as caught:
            audit_pages(pages, [broken], {})
        self.assertIn("dict_metric", str(caught.exception))
        self.assertIn("MetricResult", str(caught.exception))

    def test_nan_score_is_an_error_not_a_clean_zero(self) -> None:
        def not_a_number(page: Page, config: dict) -> MetricResult:
            return MetricResult(score=float("nan"))

        broken = Metric(id="nan_metric", summary="returns NaN", func=not_a_number)
        pages = [parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, [broken], {})
        row = report["pages"][0]
        self.assertEqual(row["status"], "error")
        self.assertIn("NaN", row["errors"][0]["error"])

    def test_error_pages_rank_before_scored_pages(self) -> None:
        (self.root / "P09.svg").write_text(PAGE, encoding="utf-8")

        def sometimes(page: Page, config: dict) -> MetricResult:
            if page.name == "P09":
                raise ValueError("boom")
            return MetricResult(score=0.9)

        metric_spec = Metric(id="sometimes", summary="fails on P09", func=sometimes)
        pages = [parse_page(self.root / "P09.svg"), parse_page(self.root / "P01.svg")]
        report = audit_pages(pages, [metric_spec], {})
        self.assertEqual([row["page"] for row in report["pages"]], ["P09", "P01"])
        self.assertEqual(report["pages"][0]["status"], "error")
        self.assertNotIn("status", report["pages"][1])


class ContrastFloorTest(unittest.TestCase):
    """Normalisation uses the floor that actually applies to the run."""

    def test_large_text_denominator_is_the_relaxed_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "P01.svg"
            path.write_text(LARGE_NEAR_MISS, encoding="utf-8")
            page = parse_page(path)
        report = audit_pages([page], select_metrics({"contrast"}), {})
        # #999999 on white is 2.85:1; required is 3.0 for 30px text.
        self.assertAlmostEqual(report["pages"][0]["scores"]["contrast"], 0.0506, delta=0.002)

    def test_translucent_paint_is_unknown_not_opaque(self) -> None:
        self.assertIsNone(parse_color("rgba(0,0,0,0.5)"))
        self.assertEqual(parse_color("rgb(0,0,0)"), (0.0, 0.0, 0.0, 1.0))

class RotatedBoundingBoxTest(unittest.TestCase):
    """An arbitrary rotation needs all four corners, not the diagonal pair."""

    def test_off_axis_rotation_uses_every_corner(self) -> None:
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            '<rect id="r" x="0" y="0" width="20" height="10" transform="rotate(45)"/>'
            "</svg>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "P01.svg"
            path.write_text(svg, encoding="utf-8")
            page = parse_page(path)
        x1, y1, x2, y2 = page.shapes()[0].bbox
        self.assertAlmostEqual(x1, -7.0711, delta=0.01)
        self.assertAlmostEqual(y1, 0.0, delta=0.01)
        self.assertAlmostEqual(x2, 14.1421, delta=0.01)
        self.assertAlmostEqual(y2, 21.2132, delta=0.01)


if __name__ == "__main__":
    unittest.main()
