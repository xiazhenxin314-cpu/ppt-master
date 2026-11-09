#!/usr/bin/env python3
"""PPT Master - Page Zoom

Render one page and crop it to one element, so a detail can be inspected at 2x,
4x, or more instead of squinting at a full slide. With --compare, two pages are
rendered and placed side by side with BEFORE / AFTER labels.

Usage:
    python3 scripts/page_zoom.py <page.svg> --id <element> --scale 4 --out zoom.png
    python3 scripts/page_zoom.py before.svg --id title-bg --compare after.svg --out cmp.png

Examples:
    python3 scripts/page_zoom.py projects/demo/svg_output/P07.svg --id title-bg --scale 4 --out /tmp/zoom.png
    python3 scripts/page_zoom.py /tmp/before.svg --id card --compare /tmp/after.svg --out /tmp/cmp.png

Dependencies:
    Pillow, plus a local Chrome for rendering. Set PPT_MASTER_CHROME to override
    the executable search.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from attribution_guard import require_skill_integrity  # noqa: E402
from console_encoding import configure_utf8_stdio  # noqa: E402
from page_checks.page import element_boxes, parse_page  # noqa: E402

configure_utf8_stdio()

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)

LABEL_HEIGHT = 44
COMPARE_GAP = 8


def find_chrome() -> str:
    """Return the Chrome executable, or raise a message naming the override."""
    override = os.environ.get("PPT_MASTER_CHROME")
    if override:
        if Path(override).is_file():
            return override
        raise RuntimeError(f"PPT_MASTER_CHROME points at {override!r}, which is not a file")
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "Chrome was not found. Install Chrome, or set PPT_MASTER_CHROME to its executable."
    )


def render(svg: Path, scale: float, width: float, height: float, destination: Path) -> None:
    """Render one SVG at `scale` and write a PNG."""
    chrome = find_chrome()
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-sandbox",
        f"--screenshot={destination}",
        f"--window-size={round(width)},{round(height)}",
        f"--force-device-scale-factor={scale:g}",
        svg.resolve().as_uri(),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if not destination.is_file():
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError("Chrome produced no image" + (f": {detail[-1]}" if detail else ""))


def crop_box(box, margin: float, scale: float, size: tuple[int, int]):
    """Return a clamped crop rectangle in rendered pixels."""
    left = (box[0] - margin) * scale
    top = (box[1] - margin) * scale
    right = (box[2] + margin) * scale
    bottom = (box[3] + margin) * scale
    return (
        max(0, int(left)),
        max(0, int(top)),
        min(size[0], int(round(right))),
        min(size[1], int(round(bottom))),
    )


def _label_tile(image, text: str):
    """Add a labelled header above one rendered crop."""
    from PIL import Image, ImageDraw, ImageFont

    tile = Image.new("RGB", (image.width, image.height + LABEL_HEIGHT), "white")
    tile.paste(image, (0, LABEL_HEIGHT))
    draw = ImageDraw.Draw(tile)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:  # older Pillow: no size argument
        font = ImageFont.load_default()
    draw.text((10, 9), text, fill="black", font=font)
    return tile


def main(argv: list[str] | None = None) -> int:
    """Render, crop, and write the requested image."""
    parser = argparse.ArgumentParser(
        description="Crop a rendered page to one element, optionally before/after.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("page", help="page svg to render")
    parser.add_argument("--id", required=True, metavar="ID", help="element to frame")
    parser.add_argument("--scale", type=float, default=4.0, help="zoom factor (default 4)")
    parser.add_argument("--margin", type=float, default=24.0, help="padding around the element in px")
    parser.add_argument("--compare", metavar="SVG", help="a second page, rendered as AFTER")
    parser.add_argument("--out", metavar="PNG", help="output image (default: beside the page)")
    args = parser.parse_args(argv)

    page_path = Path(args.page)
    if not page_path.is_file():
        print(f"not a file: {page_path}", file=sys.stderr)
        return 2
    compare_path = Path(args.compare) if args.compare else None
    if compare_path and not compare_path.is_file():
        print(f"not a file: {compare_path}", file=sys.stderr)
        return 2
    if args.scale <= 0:
        print("--scale must be greater than zero", file=sys.stderr)
        return 2

    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required: pip install Pillow", file=sys.stderr)
        return 2

    try:
        page = parse_page(page_path)
        boxes = element_boxes(page_path)
        if args.id not in boxes:
            known = ", ".join(sorted(boxes)[:20]) or "<none>"
            print(f"no element with id={args.id!r}; known ids: {known}", file=sys.stderr)
            return 2
        box = boxes[args.id]
        second = None
        if compare_path:
            other = element_boxes(compare_path)
            if args.id not in other:
                print(f"no element with id={args.id!r} in {compare_path}", file=sys.stderr)
                return 2
            second = other[args.id]
            box = (
                min(box[0], second[0]),
                min(box[1], second[1]),
                max(box[2], second[2]),
                max(box[3], second[3]),
            )

        with tempfile.TemporaryDirectory() as tmp:
            first_png = Path(tmp) / "first.png"
            render(page_path, args.scale, page.width, page.height, first_png)
            first_image = Image.open(first_png).convert("RGB")
            rect = crop_box(box, args.margin, args.scale, first_image.size)
            first_crop = first_image.crop(rect)
            if compare_path is None:
                result = first_crop
            else:
                second_png = Path(tmp) / "second.png"
                render(compare_path, args.scale, page.width, page.height, second_png)
                second_image = Image.open(second_png).convert("RGB")
                second_crop = second_image.crop(crop_box(box, args.margin, args.scale, second_image.size))
                before_tile = _label_tile(first_crop, "BEFORE")
                after_tile = _label_tile(second_crop, "AFTER")
                result = Image.new(
                    "RGB",
                    (before_tile.width + after_tile.width + COMPARE_GAP, before_tile.height),
                    "white",
                )
                result.paste(before_tile, (0, 0))
                result.paste(after_tile, (before_tile.width + COMPARE_GAP, 0))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    destination = Path(args.out) if args.out else page_path.with_name(f"{page_path.stem}-{args.id}-zoom.png")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.save(destination)
    except OSError as exc:
        print(f"cannot write {destination}: {exc}", file=sys.stderr)
        return 2
    print(f"{destination}\n{result.width}x{result.height}")
    return 0


if __name__ == "__main__":
    require_skill_integrity()
    raise SystemExit(main())
