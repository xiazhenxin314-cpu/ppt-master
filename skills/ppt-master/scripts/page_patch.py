#!/usr/bin/env python3
"""PPT Master - Page Patch

Minimal-edit repair of one authored page: change an attribute, or move and
resize one element by a relative amount, while every other byte of the file
stays identical. Geometry is arithmetic, not dragging.

Usage:
    python3 scripts/page_patch.py <page.svg> --set "P07-title:font-size=44"
    python3 scripts/page_patch.py <page.svg> --id title-bg --grow-left 20%
    python3 scripts/page_patch.py <page.svg> --id card --pad 12 --dry-run
    python3 scripts/page_patch.py <page.svg> --id title --center-x page
    python3 scripts/page_patch.py <page.svg> --measure title-bg

Examples:
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --set "P07-card:fill=#1B1F24"
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --id title-bg --grow-left 20% --out /tmp/P07.svg
    python3 scripts/page_patch.py projects/demo/svg_output/P07.svg --id title --center-x page --dry-run

Dependencies:
    Standard library plus sibling modules console_encoding, attribution_guard,
    and text_measure (for --measure).

See scripts/docs/page-check.md for the operator table and the repair loop.
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

EMU_PER_PX = 9525

# Quote-aware start tag: a `>` inside an attribute value must not end the tag.
_TAG_RE = re.compile(r"""<[A-Za-z][\w.:-]*\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.S)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*(["'])(.*?)\1""", re.S)
_ATTR_RE = re.compile(r"""(\b[A-Za-z_:][\w.:-]*\s*=\s*)(["'])(.*?)\2""", re.S)
_NAME_RE = re.compile(r"^[A-Za-z_:][\w.:-]*$")
_NUM_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)")

# Regions where a tag-shaped string is not a tag. Each is replaced by an
# equal-length run of spaces so every offset still points at the same byte.
_MASK_PATTERNS = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"<!\[CDATA\[.*?\]\]>", re.S),
    re.compile(r"<\?.*?\?>", re.S),
    re.compile(r"<!DOCTYPE(?:[^>\[]|\[[^\]]*\])*>", re.S),
)

ORDERED_FLAGS = (
    "--set",
    "--grow-left",
    "--grow-right",
    "--grow-top",
    "--grow-bottom",
    "--pad",
    "--center-x",
    "--center-y",
)

GEOMETRY_ATTRS = ("x", "y", "width", "height")


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
    root = parse_root(text)
    counts: dict[str, int] = {}
    for element in root.iter():
        value = element.get("id")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def parse_root(text: str) -> ET.Element:
    """Parse the page text, turning a parse failure into a patch error."""
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise PagePatchError(f"page is not parseable: {exc}") from exc


def _one_element(text: str, element_id: str) -> ET.Element:
    """Return the single element carrying `element_id`; reject missing/duplicate."""
    root = parse_root(text)
    matches = [element for element in root.iter() if element.get("id") == element_id]
    if not matches:
        known = ", ".join(sorted(id_counts(text))) or "<none>"
        raise PagePatchError(f"no element with id={element_id!r}; known ids: {known}")
    if len(matches) > 1:
        raise PagePatchError(f"id={element_id!r} is not unique ({len(matches)} matches)")
    return matches[0]


def _number(attrs: dict, name: str) -> float:
    """Read a geometry attribute as a float, rejecting a missing or odd value."""
    raw = attrs.get(name)
    if raw is None:
        raise PagePatchError(f"element has no {name!r} attribute")
    match = _NUM_RE.match(str(raw).strip())
    if not match:
        raise PagePatchError(f"cannot read {name}={raw!r} as a number")
    return float(match.group(0))


def _format(value: float) -> str:
    """Render a computed length without trailing zeros."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _amount(raw: str, base: float) -> float:
    """Parse `20%` against `base`, or a plain pixel number."""
    text = raw.strip()
    if text.endswith("%"):
        try:
            return base * float(text[:-1]) / 100.0
        except ValueError as exc:
            raise PagePatchError(f"cannot read percentage {raw!r}") from exc
    try:
        return float(text)
    except ValueError as exc:
        raise PagePatchError(f"cannot read length {raw!r}") from exc


def _canvas(text: str) -> tuple[float, float]:
    """Return the page canvas from the root width/height or viewBox."""
    root = parse_root(text)
    width = root.attrib.get("width", "")
    height = root.attrib.get("height", "")
    view_box = [float(number) for number in _NUM_RE.findall(root.attrib.get("viewBox", ""))]
    if len(view_box) == 4:
        return view_box[2], view_box[3]
    try:
        return float(_NUM_RE.match(width).group(0)), float(_NUM_RE.match(height).group(0))
    except (AttributeError, TypeError) as exc:
        raise PagePatchError("canvas size is missing; give the root a viewBox") from exc


def _geometry(text: str, element_id: str) -> dict[str, float]:
    """Read x/y/width/height of one element as floats."""
    element = _one_element(text, element_id)
    return {name: _number(element.attrib, name) for name in GEOMETRY_ATTRS}


def _set_attribute(text: str, element_id: str, attribute: str, value: str) -> tuple[str, str | None]:
    """Replace or insert one attribute inside the element's start tag."""
    if not _NAME_RE.match(attribute):
        raise InvalidPatchedXml(
            f"attribute name {attribute!r} is not a valid XML name; "
            'use letters, digits, ".", "-", "_" or ":", starting with a letter, "_" or ":".'
        )
    start, end = _find_start_span(text, element_id)
    tag = text[start:end]
    match = _ATTR_RE.search(tag)
    while match is not None:
        if match.group(1).strip().rstrip("=").strip() == attribute:
            previous = match.group(3)
            new_tag = tag[: match.start(3)] + _escape(value) + tag[match.end(3):]
            return text[:start] + new_tag + text[end:], previous
        match = _ATTR_RE.search(tag, match.end())
    insert_at = end - (2 if tag.endswith("/>") else 1)
    trimmed = tag[:insert_at].rstrip()
    new_tag = f'{trimmed} {attribute}="{_escape(value)}"{tag[insert_at:]}'
    return text[:start] + new_tag + text[end:], None


def _escape(value: str) -> str:
    """Escape an attribute value for XML."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


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


def start_tag(text: str, element_id: str) -> str:
    """Return the literal start tag for one id; list the known ids on failure."""
    try:
        start, end = _find_start_span(text, element_id)
    except PagePatchError as exc:
        known = ", ".join(sorted(id_counts(text))) or "<none>"
        raise PagePatchError(f"{exc}; known ids: {known}") from exc
    return text[start:end]


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


def _split_operand(raw: str, selected: str | None) -> tuple[str, str]:
    """Split an operator operand into (element id, value), honouring `--id`."""
    if ":" in raw:
        element_id, value = raw.split(":", 1)
        element_id = element_id.strip()
        if element_id:
            return element_id, value.strip()
    if not selected:
        raise PagePatchError(
            f"operator {raw!r} has no target; pass --id <element> or write <element>:<value>"
        )
    return selected, raw.strip()


def _apply_operator(
    text: str,
    flag: str,
    operand: str,
    selected: str | None,
    stage: list[Change],
) -> str:
    """Apply one relative geometry operator and record the attribute changes."""
    element_id, value = _split_operand(operand, selected)
    geometry = _geometry(text, element_id)

    def assign(attribute: str, new_value: float) -> str:
        patched, previous = _set_attribute(text, element_id, attribute, _format(new_value))
        stage.append(Change(element_id, attribute, previous, _format(new_value)))
        return patched

    if flag in {"--grow-left", "--grow-right"}:
        delta = _amount(value, geometry["width"])
        if flag == "--grow-left":
            text = assign("x", geometry["x"] - delta)
            geometry["width"] += delta
            return assign("width", geometry["width"])
        text = assign("width", geometry["width"] + delta)
        return text
    if flag in {"--grow-top", "--grow-bottom"}:
        delta = _amount(value, geometry["height"])
        if flag == "--grow-top":
            text = assign("y", geometry["y"] - delta)
            geometry["height"] += delta
            return assign("height", geometry["height"])
        return assign("height", geometry["height"] + delta)
    if flag == "--pad":
        delta = _amount(value, 0.0)
        text = assign("x", geometry["x"] - delta)
        text = assign("y", geometry["y"] - delta)
        text = assign("width", geometry["width"] + 2 * delta)
        return assign("height", geometry["height"] + 2 * delta)
    if flag in {"--center-x", "--center-y"}:
        horizontal = flag == "--center-x"
        if value == "page":
            canvas_width, canvas_height = _canvas(text)
            span = canvas_width if horizontal else canvas_height
            offset = (span - (geometry["width"] if horizontal else geometry["height"])) / 2.0
        else:
            other = _geometry(text, value)
            if horizontal:
                offset = other["x"] + (other["width"] - geometry["width"]) / 2.0
            else:
                offset = other["y"] + (other["height"] - geometry["height"]) / 2.0
        return assign("x" if horizontal else "y", offset)
    raise PagePatchError(f"unknown operator {flag!r}")


def ordered_operations(argv: Sequence[str]) -> list[tuple[str, str]]:
    """Collect operator flags and their operands in command-line order."""
    operations: list[tuple[str, str]] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        name, _, inline = token.partition("=")
        if name in ORDERED_FLAGS:
            if inline:
                operations.append((name, inline))
                index += 1
                continue
            if index + 1 >= len(argv):
                raise PagePatchError(f"{name} needs a value")
            operations.append((name, argv[index + 1]))
            index += 2
            continue
        index += 1
    return operations


def plan_operations(
    text: str,
    operations: Sequence[tuple[str, str]],
    selected: str | None,
) -> tuple[str, list[Change]]:
    """Apply `--set` and the relative operators in the order they were written."""
    patched = text
    changes: list[Change] = []
    for flag, operand in operations:
        if flag == "--set":
            patched, applied = plan_patch(patched, [operand])
            changes.extend(applied)
        else:
            patched = _apply_operator(patched, flag, operand, selected, changes)
    _require_parseable(patched, operations[-1][1] if operations else "")
    return patched, changes


def _require_parseable(text: str, context: str) -> None:
    """Reject a patch that would leave the page unparseable."""
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        raise InvalidPatchedXml(
            f"refusing to write: patch for {context!r} produces invalid XML ({exc})"
        ) from exc


def measure(text: str, element_id: str) -> str:
    """Describe one element's geometry, and a text element's measured width."""
    element = _one_element(text, element_id)
    tag = element.tag.split("}")[-1]
    lines = [f"{element_id}: <{tag}>"]
    geometry = {}
    for name in GEOMETRY_ATTRS:
        raw = element.attrib.get(name)
        if raw is None:
            continue
        geometry[name] = _number(element.attrib, name)
        lines.append(f"  {name:<7}{_format(geometry[name]):>10} px  {round(geometry[name] * EMU_PER_PX):>12} EMU")
    content = "".join(element.itertext()).strip()
    if tag == "text" and content:
        from text_measure import measure_text

        size = _number(element.attrib, "font-size") if element.attrib.get("font-size") else 16.0
        family = element.attrib.get("font-family", "Arial")
        measured = measure_text(content, size=size, family=family)
        lines.append(f"  text    {content[:30]!r} ({len(content)} chars, {_format(size)} px)")
        lines.append(f"  measured{measured:>10.1f} px  (estimator, headroom included)")
        lines.append(f"  right edge{geometry['x'] + measured:>10.1f} px  (x + measured)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Change attributes or move/resize one element; every other byte survives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("page", help="one page svg")
    parser.add_argument("--set", dest="assignments", action="append", default=[], metavar="ID:ATTR=VALUE")
    parser.add_argument("--id", dest="selected", metavar="ID", help="target element for the operators")
    parser.add_argument("--grow-left", action="append", default=[], metavar="VALUE")
    parser.add_argument("--grow-right", action="append", default=[], metavar="VALUE")
    parser.add_argument("--grow-top", action="append", default=[], metavar="VALUE")
    parser.add_argument("--grow-bottom", action="append", default=[], metavar="VALUE")
    parser.add_argument("--pad", action="append", default=[], metavar="VALUE")
    parser.add_argument("--center-x", action="append", default=[], metavar="PAGE|ID")
    parser.add_argument("--center-y", action="append", default=[], metavar="PAGE|ID")
    parser.add_argument("--measure", metavar="ID", help="print geometry and measured text width, then exit")
    parser.add_argument("--show", metavar="ID", help="print the element's start tag, then exit")
    parser.add_argument("--dry-run", action="store_true", help="report the changes, write nothing")
    parser.add_argument("--out", help="write here instead of in place")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Apply the requested edits and return an exit code."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(args_list)
    path = Path(args.page)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    try:
        text = read_page(path)
        if args.show:
            print(start_tag(text, args.show))
            return 0
        if args.measure:
            print(measure(text, args.measure))
            return 0
        operations = ordered_operations(args_list)
        if not operations:
            parser.print_help()
            return 2
        patched, changes = plan_operations(text, operations, args.selected)
    except InvalidPatchedXml as exc:
        print(str(exc), file=sys.stderr)
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
    try:
        if args.out:
            destination.parent.mkdir(parents=True, exist_ok=True)
        apply_patch(destination, patched)
    except OSError as exc:
        print(f"cannot write {destination}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {destination} ({len(changes)} change(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
