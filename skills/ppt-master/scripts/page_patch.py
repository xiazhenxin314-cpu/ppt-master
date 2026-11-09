#!/usr/bin/env python3
"""PPT Master - Page Patch

Minimal-edit repair of one authored page: change one attribute on one node and
leave every other byte of the file alone. The write side of the repair loop --
a surgical diff, not a redraw.

Usage:
    python3 scripts/page_patch.py <page.svg> --set "P07-title:font-size=44"
    python3 scripts/page_patch.py <page.svg> --set "bg:fill=#0F172A" --dry-run
    python3 scripts/page_patch.py <page.svg> --show P07-title

Examples:
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --set "P07-card:fill=#1B1F24"
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --set "bg:fill=#0F172A" --dry-run
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --set "P07-title:font-size=44" --out /tmp/P07.svg

Dependencies:
    None (only uses standard library)

See scripts/docs/aesthetic-audit.md for the repair loop this feeds.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple, Sequence
from xml.etree import ElementTree as ET

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

# Quote-aware start tag: a `>` inside an attribute value must not end the tag.
_TAG_RE = re.compile(r"""<[A-Za-z][\w.:-]*\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.S)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*(["'])(.*?)\1""", re.S)
_ATTR_RE = re.compile(r"""(\b[A-Za-z_:][\w.:-]*\s*=\s*)(["'])(.*?)\2""", re.S)
_NAME_RE = re.compile(r"^[A-Za-z_:][\w.:-]*$")

# Regions where a tag-shaped string is not a tag. Each is replaced by an
# equal-length run of spaces so every offset still points at the same byte.
_MASK_PATTERNS = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"<!\[CDATA\[.*?\]\]>", re.S),
    re.compile(r"<\?.*?\?>", re.S),
    re.compile(r"<!DOCTYPE(?:[^>\[]|\[[^\]]*\])*>", re.S),
)


class PagePatchError(ValueError):
    """A patch request that cannot be applied; nothing is written."""


class InvalidPatchedXml(PagePatchError):
    """The requested patch would produce text that is no longer valid XML."""


class Change(NamedTuple):
    """One applied attribute change."""

    element_id: str
    attribute: str
    previous: str | None
    value: str


def read_page(path: Path) -> str:
    """Read a page without newline translation so untouched bytes stay identical."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def apply_patch(path: Path, text: str) -> None:
    """Write the patched page; the only side effect in this module."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def plan_patch(text: str, assignments: Sequence[str]) -> tuple[str, list[Change]]:
    """Apply `<element-id>:<attribute>=<value>` assignments and return the result."""
    counts = id_counts(text)
    changes: list[Change] = []
    patched = text
    for raw in assignments:
        element_id, attribute, value = parse_assignment(raw)
        if counts.get(element_id, 0) == 0:
            raise PagePatchError(f"no element with id={element_id!r}")
        if counts[element_id] > 1:
            raise PagePatchError(f"id={element_id!r} is not unique ({counts[element_id]} matches)")
        patched, previous = _set_attribute(patched, element_id, attribute, value)
        changes.append(Change(element_id, attribute, previous, value))
    _require_parseable(patched, assignments[-1] if assignments else "")
    return patched, changes


def parse_assignment(raw: str) -> tuple[str, str, str]:
    """Split `<element-id>:<attribute>=<value>` and reject malformed input."""
    if ":" not in raw or "=" not in raw:
        raise PagePatchError(f"expected <element-id>:<attribute>=<value>, got {raw!r}")
    element_id, tail = raw.split(":", 1)
    attribute, value = tail.split("=", 1)
    element_id, attribute = element_id.strip(), attribute.strip()
    if not element_id:
        raise PagePatchError(f"missing element id in {raw!r}")
    if not attribute:
        raise PagePatchError(f"missing attribute name in {raw!r}")
    return element_id, attribute, value


def id_counts(text: str) -> dict[str, int]:
    """Count `id` attributes across the document using a real XML parse."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PagePatchError(f"page is not parseable: {exc}") from exc
    counts: dict[str, int] = {}
    for element in root.iter():
        value = element.get("id")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def start_tag(text: str, element_id: str) -> str:
    """Return the literal start tag for one id; list the known ids on failure."""
    try:
        start, end = _find_start_span(text, element_id)
    except PagePatchError as exc:
        known = ", ".join(sorted(id_counts(text))) or "<none>"
        raise PagePatchError(f"{exc}; known ids: {known}") from exc
    return text[start:end]


def _mask_markup(text: str) -> str:
    """Blank comments, CDATA, processing instructions, and doctypes, same length."""
    masked = text
    for pattern in _MASK_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def _find_start_span(text: str, element_id: str) -> tuple[int, int]:
    """Offset of the start tag that really carries `element_id`.

    Matching runs on the masked copy, so a commented-out or quoted tag never
    wins; offsets index the original text unchanged.
    """
    masked = _mask_markup(text)
    for match in _TAG_RE.finditer(masked):
        tag = masked[match.start():match.end()]
        id_match = _ID_ATTR_RE.search(tag)
        if id_match and id_match.group(2) == element_id:
            return match.start(), match.end()
    raise PagePatchError(f"no element with id={element_id!r}")


def _set_attribute(text: str, element_id: str, attribute: str, value: str) -> tuple[str, str | None]:
    """Replace or insert one attribute inside the element's start tag."""
    if not _NAME_RE.match(attribute):
        raise InvalidPatchedXml(
            f"attribute name {attribute!r} is not a valid XML name; "
            "use letters, digits, \".\", \"-\", \"_\" or \":\", starting with a letter, \"_\" or \":\""
        )
    start, end = _find_start_span(text, element_id)
    tag = text[start:end]
    escaped = _escape_attribute(value)
    attribute_match = None
    for candidate in _ATTR_RE.finditer(tag):
        if candidate.group(1).strip().startswith(attribute):
            name = candidate.group(1).strip().rstrip("=").strip()
            if name == attribute:
                attribute_match = candidate
                break
    if attribute_match is not None:
        previous = attribute_match.group(3)
        replacement = f'{attribute}="{escaped}"'
        new_tag = tag[: attribute_match.start()] + replacement + tag[attribute_match.end():]
    else:
        previous = None
        closing = "/>" if tag.endswith("/>") else ">"
        tail_start = len(tag) - len(closing)
        new_tag = tag[:tail_start].rstrip() + f' {attribute}="{escaped}"' + tag[tail_start:]
    return text[:start] + new_tag + text[end:], previous


def _escape_attribute(value: str) -> str:
    """Escape one attribute value for double-quoted XML output."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _require_parseable(text: str, assignment: str) -> None:
    """Refuse a patch whose result would not parse, naming the request at fault."""
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        raise InvalidPatchedXml(
            f"patch produces invalid XML for {assignment!r}: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Change one attribute on one node, preserving every other byte.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("page", help="one page svg")
    parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="ID:ATTR=VALUE",
        help="repeat to batch several attributes on one page",
    )
    parser.add_argument("--show", metavar="ID", help="print one element's start tag and exit")
    parser.add_argument("--dry-run", action="store_true", help="report the changes, write nothing")
    parser.add_argument("--out", help="write here instead of in place")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one page patch and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    path = Path(args.page)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    text = read_page(path)
    if args.show:
        try:
            print(start_tag(text, args.show))
        except PagePatchError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if not args.assignments:
        parser.print_help()
        return 2
    try:
        patched, changes = plan_patch(text, args.assignments)
    except InvalidPatchedXml as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 3
    except PagePatchError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for change in changes:
        previous = "<absent>" if change.previous is None else change.previous
        print(f"{change.element_id}.{change.attribute}: {previous} -> {change.value}")
    if args.dry_run:
        print("dry-run: nothing written", file=sys.stderr)
        return 0
    destination = Path(args.out) if args.out else path
    if not destination.parent.is_dir():
        print(f"output directory does not exist: {destination.parent}", file=sys.stderr)
        return 2
    try:
        apply_patch(destination, patched)
    except OSError as exc:
        print(f"cannot write {destination}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {destination} ({len(changes)} change(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
