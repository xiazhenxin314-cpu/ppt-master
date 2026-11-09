#!/usr/bin/env python3
"""Page model for the aesthetic audit.

Parses one page SVG into positioned primitives with composed transforms, and
owns the small amount of colour arithmetic the metrics share.

Usage:
    Imported by aesthetic_quality.metrics and aesthetic_quality.cli.

Examples:
    page = parse_page(Path("projects/demo/svg_output/P01.svg"))
    page.background()

Dependencies:
    None (only uses standard library)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

ID_MATRIX = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
SHAPE_TAGS = frozenset({"rect", "circle", "ellipse", "line", "polygon", "polyline", "text", "image"})

_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUM_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"rgba?\(([^)]+)\)")
_NAMED_COLORS = {
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "grey": (128, 128, 128),
    "red": (255, 0, 0),
    "white": (255, 255, 255),
}


@dataclass
class Node:
    """One positioned SVG primitive in page coordinates."""

    tag: str
    id: str | None
    attrs: dict
    bbox: tuple[float, float, float, float]
    fill: str | None
    font_size: float
    text: str
    depth: int


@dataclass
class Page:
    """One parsed page; `error` marks a page that could not be parsed."""

    path: Path
    width: float
    height: float
    nodes: list[Node] = field(default_factory=list)
    root_attrs: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def name(self) -> str:
        """Page stem, used as the report key."""
        return self.path.stem

    def shapes(self, tags: set[str] | None = None) -> list[Node]:
        """Return positioned primitives, optionally filtered by tag."""
        return [node for node in self.nodes if tags is None or node.tag in tags]

    def text_nodes(self) -> list[Node]:
        """Return positioned text primitives that carry visible characters."""
        return [node for node in self.nodes if node.tag == "text" and node.text]

    def background(self) -> str | None:
        """First full-canvas rectangle's paint, else the root element's own fill."""
        for node in self.nodes:
            if node.tag != "rect" or not node.fill:
                continue
            x0, y0, x1, y1 = node.bbox
            if x1 - x0 >= self.width * 0.98 and y1 - y0 >= self.height * 0.98:
                return node.fill
        return self.root_attrs.get("fill")


def parse_page(path: Path) -> Page:
    """Parse one page SVG; only `ElementTree.ParseError` escapes."""
    root = ET.parse(str(path)).getroot()
    width = _num(root.attrib, "width")
    height = _num(root.attrib, "height")
    view_box = [float(number) for number in _NUM_RE.findall(root.attrib.get("viewBox", ""))]
    if len(view_box) == 4:
        width, height = view_box[2], view_box[3]
    nodes: list[Node] = []
    _walk(root, ID_MATRIX, None, 16.0, nodes, 0)
    return Page(path=path, width=width, height=height, nodes=nodes, root_attrs=dict(root.attrib))


def _walk(elem, matrix, inherited_fill, inherited_size, out: list[Node], depth: int) -> None:
    """Collect positioned primitives depth-first, composing transforms."""
    tag = elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""
    attrs = dict(elem.attrib)
    matrix = _matrix_multiply(matrix, _parse_transform(attrs.get("transform")))
    fill = attrs.get("fill", inherited_fill)
    size = _num(attrs, "font-size", inherited_size) if "font-size" in attrs else inherited_size
    text = "".join(elem.itertext()).strip() if tag == "text" else ""
    bbox = _text_bbox(attrs, text) if tag == "text" else _raw_bbox(tag, attrs)
    if bbox is not None and matrix != ID_MATRIX:
        # A rotated box has no axis-aligned diagonal: map all four corners and
        # take the extremes, otherwise an arbitrary rotation reports a box that
        # is smaller than the shape it contains.
        x1, y1, x2, y2 = bbox
        corners = (
            _apply(matrix, x1, y1),
            _apply(matrix, x2, y1),
            _apply(matrix, x1, y2),
            _apply(matrix, x2, y2),
        )
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    if bbox is not None and tag in SHAPE_TAGS:
        out.append(
            Node(
                tag=tag,
                id=attrs.get("id"),
                attrs=attrs,
                bbox=bbox,
                fill=fill,
                font_size=size,
                text=text,
                depth=depth,
            )
        )
    for child in elem:
        if isinstance(child.tag, str):
            _walk(child, matrix, fill, size, out, depth + 1)


def _matrix_multiply(left, right):
    """Compose two 2x3 affine matrices; right is applied first."""
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _parse_transform(value: str | None):
    """Return the affine matrix of an SVG transform list."""
    matrix = ID_MATRIX
    if not value:
        return matrix
    for name, raw in _TRANSFORM_RE.findall(value):
        numbers = [float(number) for number in _NUM_RE.findall(raw)]
        if not numbers:
            continue
        if name == "matrix" and len(numbers) >= 6:
            step = tuple(numbers[:6])
        elif name == "translate":
            step = (1.0, 0.0, 0.0, 1.0, numbers[0], numbers[1] if len(numbers) > 1 else 0.0)
        elif name == "scale":
            scale_x = numbers[0]
            scale_y = numbers[1] if len(numbers) > 1 else scale_x
            step = (scale_x, 0.0, 0.0, scale_y, 0.0, 0.0)
        elif name == "rotate":
            angle = math.radians(numbers[0])
            rotation = (
                math.cos(angle),
                math.sin(angle),
                -math.sin(angle),
                math.cos(angle),
                0.0,
                0.0,
            )
            if len(numbers) >= 3:
                # rotate(a, cx, cy) == translate(cx, cy) rotate(a) translate(-cx, -cy)
                centre = (1.0, 0.0, 0.0, 1.0, numbers[1], numbers[2])
                back = (1.0, 0.0, 0.0, 1.0, -numbers[1], -numbers[2])
                step = _matrix_multiply(_matrix_multiply(centre, rotation), back)
            else:
                step = rotation
        else:
            continue
        matrix = _matrix_multiply(matrix, step)
    return matrix


def _apply(matrix, x: float, y: float) -> tuple[float, float]:
    """Map one point through an affine matrix."""
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _num(attrs: dict, key: str, default: float = 0.0) -> float:
    """Read a length attribute as a float, ignoring any unit suffix."""
    raw = attrs.get(key)
    if raw is None:
        return default
    match = _NUM_RE.match(str(raw).strip())
    return float(match.group(0)) if match else default


def _raw_bbox(tag: str, attrs: dict):
    """Return the untransformed bounding box of one primitive, or None."""
    if tag == "rect":
        x, y = _num(attrs, "x"), _num(attrs, "y")
        return (x, y, x + _num(attrs, "width"), y + _num(attrs, "height"))
    if tag in {"circle", "ellipse"}:
        cx, cy = _num(attrs, "cx"), _num(attrs, "cy")
        radius_x = _num(attrs, "r") if tag == "circle" else _num(attrs, "rx")
        radius_y = _num(attrs, "r") if tag == "circle" else _num(attrs, "ry")
        return (cx - radius_x, cy - radius_y, cx + radius_x, cy + radius_y)
    if tag == "line":
        x1, y1 = _num(attrs, "x1"), _num(attrs, "y1")
        x2, y2 = _num(attrs, "x2"), _num(attrs, "y2")
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag in {"polygon", "polyline"}:
        numbers = [float(number) for number in _NUM_RE.findall(attrs.get("points", ""))]
        if len(numbers) < 4:
            return None
        xs, ys = numbers[0::2], numbers[1::2]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _text_bbox(attrs: dict, text: str):
    """Estimate a text box from the anchor, font size, and glyph count."""
    x, y = _num(attrs, "x"), _num(attrs, "y")
    size = _num(attrs, "font-size", 16.0)
    width = max(len(text), 1) * size * 0.58
    return (x, y - size * 0.82, x + width, y + size * 0.22)


def parse_color(value: str | None):
    """Return (r, g, b, a) in 0..1 for flat opaque paint, else None."""
    if not value:
        return None
    value = value.strip()
    if value.startswith("url(") or value in {"none", "transparent", "currentColor"}:
        return None
    if value.lower() in _NAMED_COLORS:
        red, green, blue = _NAMED_COLORS[value.lower()]
        return (red / 255, green / 255, blue / 255, 1.0)
    match = _HEX_RE.match(value)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(channel * 2 for channel in digits)
        return (
            int(digits[0:2], 16) / 255,
            int(digits[2:4], 16) / 255,
            int(digits[4:6], 16) / 255,
            1.0,
        )
    match = _RGB_RE.match(value)
    if match:
        parts = [part.strip() for part in match.group(1).replace("/", ",").split(",")]
        if len(parts) < 3:
            return None
        channels = []
        for part in parts[:3]:
            number = float(part.replace("%", ""))
            channels.append(number / 100 if part.endswith("%") else number / 255)
        alpha = 1.0
        if len(parts) >= 4:
            alpha = float(parts[3].replace("%", ""))
            alpha = alpha / 100 if parts[3].endswith("%") else alpha
        if alpha < 1.0:
            # Translucent paint has no single colour until it is composited; the
            # caller must not guess a background, so report "unknown paint".
            return None
        return (channels[0], channels[1], channels[2], 1.0)
    return None


def _luminance(rgb) -> float:
    """WCAG relative luminance of a parsed colour."""

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = rgb[:3]
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground, background) -> float:
    """WCAG contrast ratio between two parsed colours."""
    high = max(_luminance(foreground), _luminance(background))
    low = min(_luminance(foreground), _luminance(background))
    return (high + 0.05) / (low + 0.05)
