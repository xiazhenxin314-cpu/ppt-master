#!/usr/bin/env python3
"""Regressions for minimal-edit page patching."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from page_patch import (  # noqa: E402
    Change,
    InvalidPatchedXml,
    PagePatchError,
    id_counts,
    main,
    plan_patch,
    start_tag,
)

PAGE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">\n'
    '  <rect id="card" x="8" y="8" width="40" height="24" fill="#111111"/>\n'
    '  <text id="t" x="8" y="60" font-size="12" fill="#222222">hi</text>\n'
    '</svg>\n'
)

COMMENTED = PAGE.replace(
    '  <rect id="card"',
    '  <!-- <rect id="card" x="0" y="0" width="1" height="1" fill="#000000"/> -->\n'
    '  <rect id="card"',
)
DUPLICATE = PAGE.replace('<text id="t"', '<text id="card"')
BROKEN = '<svg xmlns="http://www.w3.org/2000/svg"><rect id="card" x="8"'


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run main() with captured streams."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class PlanPatchTest(unittest.TestCase):
    """Pure planning: what changes, and what refuses to change."""

    def test_replaces_only_the_target_attribute(self) -> None:
        patched, changes = plan_patch(PAGE, ["t:font-size=14"])
        self.assertEqual(patched, PAGE.replace('font-size="12"', 'font-size="14"'))
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual((change.element_id, change.attribute), ("t", "font-size"))
        self.assertEqual((change.previous, change.value), ("12", "14"))

    def test_inserts_an_absent_attribute(self) -> None:
        patched, changes = plan_patch(PAGE, ["card:opacity=0.5"])
        self.assertIn('opacity="0.5"', patched)
        self.assertIsNone(changes[0].previous)
        ET.fromstring(patched)

    def test_batches_multiple_assignments(self) -> None:
        patched, changes = plan_patch(PAGE, ["t:font-size=14", "t:fill=#FFFFFF"])
        self.assertEqual(len(changes), 2)
        self.assertIn('font-size="14"', patched)
        self.assertIn('fill="#FFFFFF"', patched)
        self.assertEqual(patched.count('fill="#FFFFFF"'), 1)

    def test_duplicate_id_is_rejected(self) -> None:
        self.assertEqual(id_counts(DUPLICATE)["card"], 2)
        with self.assertRaises(PagePatchError):
            plan_patch(DUPLICATE, ["card:fill=#000000"])

    def test_missing_id_is_rejected(self) -> None:
        with self.assertRaises(PagePatchError) as caught:
            plan_patch(PAGE, ["nope:fill=#000000"])
        self.assertIn("no element with id=", str(caught.exception))

    def test_malformed_assignment_is_rejected(self) -> None:
        with self.assertRaises(PagePatchError):
            plan_patch(PAGE, ["font-size=14"])

    def test_invalid_attribute_name_produces_invalid_xml(self) -> None:
        with self.assertRaises(InvalidPatchedXml):
            plan_patch(PAGE, ["card:foo bar=1"])

    def test_unparseable_page_is_rejected(self) -> None:
        with self.assertRaises(PagePatchError):
            id_counts(BROKEN)

    def test_escaping_keeps_xml_valid(self) -> None:
        patched, _ = plan_patch(PAGE, ['t:data-note=a&b"c'])
        self.assertIn('data-note="a&amp;b&quot;c"', patched)
        ET.fromstring(patched)


class CliTest(unittest.TestCase):
    """The single side effect: writing the page."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.page = self.root / "P07.svg"
        self.page.write_bytes(PAGE.encode("utf-8"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_writes_nothing(self) -> None:
        before = self.page.read_bytes()
        code, stdout, stderr = run_cli([str(self.page), "--set", "t:font-size=14", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("t.font-size: 12 -> 14", stdout)
        self.assertIn("dry-run", stderr)
        self.assertEqual(self.page.read_bytes(), before)

    def test_write_changes_one_region_only(self) -> None:
        code, stdout, stderr = run_cli([str(self.page), "--set", "t:font-size=14"])
        self.assertEqual(code, 0)
        self.assertIn("t.font-size: 12 -> 14", stdout)
        self.assertIn("wrote", stderr)
        self.assertEqual(
            self.page.read_bytes(),
            PAGE.replace('font-size="12"', 'font-size="14"').encode("utf-8"),
        )

    def test_crlf_and_untouched_bytes_survive(self) -> None:
        crlf = PAGE.replace("\n", "\r\n")
        self.page.write_bytes(crlf.encode("utf-8"))
        code, _, _ = run_cli([str(self.page), "--set", "t:font-size=14"])
        self.assertEqual(code, 0)
        expected = crlf.replace('font-size="12"', 'font-size="14"').encode("utf-8")
        self.assertEqual(self.page.read_bytes(), expected)

    def test_out_writes_elsewhere_and_leaves_source(self) -> None:
        before = self.page.read_bytes()
        destination = self.root / "P07-patched.svg"
        code, _, _ = run_cli(
            [str(self.page), "--set", "t:font-size=14", "--out", str(destination)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.page.read_bytes(), before)
        self.assertIn('font-size="14"', destination.read_text(encoding="utf-8"))

    def test_show_prints_start_tag(self) -> None:
        code, stdout, _ = run_cli([str(self.page), "--show", "t"])
        self.assertEqual(code, 0)
        self.assertIn('<text id="t"', stdout)

    def test_duplicate_id_exits_two(self) -> None:
        self.page.write_bytes(DUPLICATE.encode("utf-8"))
        code, _, stderr = run_cli([str(self.page), "--set", "card:fill=#000000"])
        self.assertEqual(code, 2)
        self.assertIn("not unique", stderr)

    def test_invalid_patch_exits_three(self) -> None:
        before = self.page.read_bytes()
        code, _, stderr = run_cli([str(self.page), "--set", "card:foo bar=1"])
        self.assertEqual(code, 3)
        self.assertIn("refusing to write", stderr)
        self.assertEqual(self.page.read_bytes(), before)

    def test_missing_file_exits_two(self) -> None:
        code, _, stderr = run_cli([str(self.root / "absent.svg"), "--set", "t:fill=#000000"])
        self.assertEqual(code, 2)
        self.assertIn("not a file", stderr)


class CommentMaskingTest(unittest.TestCase):
    """A tag that only exists inside a comment must never be the patch target."""

    def test_comment_shaped_tag_is_not_matched(self) -> None:
        patched, changes = plan_patch(COMMENTED, ["card:fill=#2A2A2A"])
        self.assertEqual(changes[0].element_id, "card")
        self.assertIn('<rect id="card" x="0" y="0" width="1" height="1" fill="#000000"/>', patched)
        self.assertIn('fill="#2A2A2A"', patched)
        self.assertEqual(patched.count('fill="#2A2A2A"'), 1)

    def test_cli_patches_the_real_element_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "P07.svg"
            page.write_bytes(COMMENTED.encode("utf-8"))
            code, stdout, _ = run_cli([str(page), "--set", "card:fill=#2A2A2A"])
            written = page.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("card.fill: #111111 -> #2A2A2A", stdout)
        self.assertIn('fill="#000000"', written)
        self.assertEqual(written.count('fill="#2A2A2A"'), 1)

    def test_cdata_and_pi_are_masked_too(self) -> None:
        page = PAGE.replace(
            "  <rect id=\"card\"",
            '  <?note <rect id="card" fill="#000000"/>?>\n'
            '  <rect id="card"',
        )
        patched, _ = plan_patch(page, ["card:fill=#333333"])
        self.assertIn('<?note <rect id="card" fill="#000000"/>?>', patched)
        self.assertEqual(patched.count('fill="#333333"'), 1)


class DiagnosticsTest(unittest.TestCase):
    """Refusals name the cause instead of echoing a parser error alone."""

    def test_invalid_attribute_name_is_explained(self) -> None:
        with self.assertRaises(InvalidPatchedXml) as caught:
            plan_patch(PAGE, ["card:foo bar=1"])
        message = str(caught.exception)
        self.assertIn("foo bar", message)
        self.assertIn("not a valid XML name", message)

    def test_unknown_show_lists_known_ids(self) -> None:
        with self.assertRaises(PagePatchError) as caught:
            start_tag(PAGE, "nope")
        message = str(caught.exception)
        self.assertIn("known ids", message)
        self.assertIn("card", message)

    def test_angle_bracket_in_value_is_escaped_not_rejected(self) -> None:
        patched, _ = plan_patch(PAGE, ["t:data-note=a>b<c"])
        self.assertIn('data-note="a&gt;b&lt;c"', patched)
        ET.fromstring(patched)

    def test_change_is_a_named_tuple(self) -> None:
        _, changes = plan_patch(PAGE, ["t:font-size=14"])
        self.assertIsInstance(changes[0], Change)
        self.assertEqual(changes[0]._fields, ("element_id", "attribute", "previous", "value"))
        self.assertEqual(tuple(changes[0]), ("t", "font-size", "12", "14"))


class OutputPathTest(unittest.TestCase):
    """A bad destination is a message, never a traceback."""

    def test_missing_output_directory_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "P07.svg"
            page.write_bytes(PAGE.encode("utf-8"))
            destination = root / "absent" / "P07.svg"
            code, _, stderr = run_cli(
                [str(page), "--set", "t:font-size=14", "--out", str(destination)]
            )
            self.assertEqual(code, 2)
            self.assertIn("output directory does not exist", stderr)
            self.assertFalse(destination.exists())

if __name__ == "__main__":
    unittest.main()
