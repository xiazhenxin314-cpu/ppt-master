#!/usr/bin/env python3
"""Geometry operators, byte preservation, and exit codes for page_patch."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from page_patch import main  # noqa: E402

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <rect id="card" x="200" y="100" width="300" height="100" fill="#C00000"/>
  <text id="title" x="240" y="160" font-size="48" fill="#FFFFFF">失分形态</text>
</svg>
"""


def run(args: list[str]) -> tuple[int, str, str]:
    """Run the CLI, capturing both streams."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class PatchTestCase(unittest.TestCase):
    """Each test starts from a fresh copy of the fixture."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "P01.svg"
        self.path.write_text(PAGE, encoding="utf-8")

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


class GrowTest(PatchTestCase):
    """Relative growth moves one edge and keeps the opposite edge fixed."""

    def test_grow_left_percentage_uses_current_width(self) -> None:
        code, out, _ = run([str(self.path), "--id", "card", "--grow-left", "20%"])
        self.assertEqual(code, 0)
        self.assertIn("card.x: 200 -> 140", out)
        self.assertIn("card.width: 300 -> 360", out)
        self.assertIn('id="card" x="140" y="100" width="360"', self.text())

    def test_grow_right_adds_pixels(self) -> None:
        code, _, _ = run([str(self.path), "--id", "card", "--grow-right", "10"])
        self.assertEqual(code, 0)
        self.assertIn('x="200" y="100" width="310"', self.text())

    def test_pad_expands_all_four_sides(self) -> None:
        code, _, _ = run([str(self.path), "--id", "card", "--pad", "10"])
        self.assertEqual(code, 0)
        self.assertIn('x="190" y="90" width="320" height="120"', self.text())

    def test_inline_target_needs_no_id_flag(self) -> None:
        code, _, _ = run([str(self.path), "--grow-left", "card:20%"])
        self.assertEqual(code, 0)
        self.assertIn('x="140"', self.text())

    def test_only_the_touched_attributes_change(self) -> None:
        run([str(self.path), "--id", "card", "--grow-left", "20%"])
        before = PAGE
        after = self.text()
        self.assertEqual(after.replace('x="140"', 'x="200"').replace('width="360"', 'width="300"'), before)


class CenterTest(PatchTestCase):
    """Centring uses the page canvas or another element as the reference."""

    def test_center_x_on_page(self) -> None:
        code, _, _ = run([str(self.path), "--id", "card", "--center-x", "page"])
        self.assertEqual(code, 0)
        self.assertIn('x="490"', self.text())

    def test_center_y_on_page(self) -> None:
        code, _, _ = run([str(self.path), "--id", "card", "--center-y", "page"])
        self.assertEqual(code, 0)
        self.assertIn('y="310"', self.text())

    def test_center_x_on_another_element(self) -> None:
        body = PAGE.replace(
            '<text id="title"',
            '<rect id="panel" x="700" y="400" width="500" height="80" fill="#EEEEEE"/>\n  <text id="title"',
        )
        self.path.write_text(body, encoding="utf-8")
        code, _, _ = run([str(self.path), "--id", "card", "--center-x", "panel"])
        self.assertEqual(code, 0)
        # panel centre is 950; a 300-wide card centred there starts at 800.
        self.assertIn('x="800"', self.text())


class OrderAndSafetyTest(PatchTestCase):
    """Operations apply in command order, and a bad request writes nothing."""

    def test_set_then_grow_in_one_command(self) -> None:
        code, out, _ = run([
            str(self.path), "--id", "card",
            "--set", "card:fill=#00FF00",
            "--grow-left", "50",
        ])
        self.assertEqual(code, 0)
        self.assertIn("card.fill: #C00000 -> #00FF00", out)
        self.assertIn('x="150"', self.text())

    def test_dry_run_writes_nothing(self) -> None:
        code, out, err = run([str(self.path), "--id", "card", "--grow-left", "20%", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("card.x: 200 -> 140", out)
        self.assertIn("dry-run", err)
        self.assertEqual(self.text(), PAGE)

    def test_bad_percentage_exits_two(self) -> None:
        code, _, err = run([str(self.path), "--id", "card", "--grow-left", "20%%"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read percentage", err)
        self.assertEqual(self.text(), PAGE)

    def test_operator_without_target_exits_two(self) -> None:
        code, _, err = run([str(self.path), "--grow-left", "20%"])
        self.assertEqual(code, 2)
        self.assertIn("no target", err)

    def test_commented_duplicate_id_is_not_patched(self) -> None:
        body = PAGE.replace(
            '<rect id="card"',
            '<!-- <rect id="card" x="1" y="1" width="9" height="9" fill="#000"/> -->\n  <rect id="card"',
        )
        self.path.write_text(body, encoding="utf-8")
        code, _, _ = run([str(self.path), "--set", "card:fill=#00FF00"])
        self.assertEqual(code, 0)
        self.assertIn('<!-- <rect id="card" x="1" y="1" width="9" height="9" fill="#000"/> -->', self.text())
        self.assertIn('id="card" x="200" y="100" width="300" height="100" fill="#00FF00"', self.text())

    def test_invalid_attribute_name_exits_three(self) -> None:
        code, _, err = run([str(self.path), "--set", "card:bad name=1"])
        self.assertEqual(code, 3)
        self.assertIn("not a valid XML name", err)


class MeasureTest(PatchTestCase):
    """--measure reports geometry in px and EMU, plus a measured text width."""

    def test_measure_rect(self) -> None:
        code, out, _ = run([str(self.path), "--measure", "card"])
        self.assertEqual(code, 0)
        self.assertIn("card: <rect>", out)
        self.assertIn("width", out)
        self.assertIn("EMU", out)

    def test_measure_text_reports_estimated_width(self) -> None:
        code, out, _ = run([str(self.path), "--measure", "title"])
        self.assertEqual(code, 0)
        self.assertIn("measured", out)
        self.assertIn("right edge", out)


class ZoomCropTest(unittest.TestCase):
    """Crop maths only: rendering is Chrome's job and is not unit-tested."""

    def test_crop_adds_margin_and_scales(self) -> None:
        from page_zoom import crop_box

        self.assertEqual(crop_box((10, 10, 110, 60), 24, 2, (2560, 1440)), (0, 0, 268, 168))

    def test_crop_clamps_to_the_image(self) -> None:
        from page_zoom import crop_box

        self.assertEqual(crop_box((1200, 700, 1280, 720), 24, 1, (1280, 720)), (1176, 676, 1280, 720))


if __name__ == "__main__":
    unittest.main()
