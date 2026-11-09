# Page Check, Page Patch, and Page Zoom

Three opt-in tools for one loop: find a factual problem on a page, change the
geometry by an exact amount, then look at the result enlarged. None of them
scores a design, and none of them changes what `svg_to_pptx.py` exports.

Dependencies: standard library, Pillow (zoom only), and a local Chrome (zoom only).

## Quick reference

| Task | Command | Output |
|---|---|---|
| List findings | `python3 scripts/page_check.py <project>` | one row per page with a finding count |
| Write the report | `python3 scripts/page_check.py <project> --json validation/page_check.json` | JSON plus `report: <path>` on stderr |
| List check ids | `python3 scripts/page_check.py --list` | one line per check |
| Move one element | `python3 scripts/page_patch.py P07.svg --id title-bg --grow-left 20%` | `id.attr: old -> new` on stdout |
| Measure one element | `python3 scripts/page_patch.py P07.svg --measure title-bg` | px and EMU, plus measured text width |
| Zoom one element | `python3 scripts/page_zoom.py P07.svg --id title-bg --scale 4 --out zoom.png` | PNG path and pixel size |

## `page_check.py`

```bash
python3 scripts/page_check.py projects/demo
python3 scripts/page_check.py projects/demo --json validation/page_check.json
python3 scripts/page_check.py projects/demo --only off_canvas --top 5
```

Page discovery: `<project>/svg_output/` when it exists, otherwise the directory
or file given, ordered by `slide_roster.discover_slide_svgs`.

| Flag | Meaning |
|---|---|
| `--json PATH` | write the full report; parent directories are created |
| `--only IDS` | comma-separated check ids |
| `--min-contrast N` | WCAG floor for body text, default `4.5` |
| `--top N` | rows printed to stdout, default `10` |
| `--list` | print check ids and summaries, then exit `0` |

Exit codes: `0` table printed; `2` no target, no SVG page, or `--only` naming no
registered check.

### Report

```json
{
  "schema": "ppt-master.page-check.v1",
  "checks": ["contrast", "off_canvas"],
  "pages": [
    {
      "page": "P07",
      "findings": [
        {"check": "contrast", "id": "p07-foot", "message": "text is 4.48:1 on #FFFFFF, below 4.5:1",
         "ratio": 4.48, "required": 4.5, "background": "#FFFFFF"}
      ],
      "canvas": [1280, 720],
      "detail": {"contrast": {"skipped": {"image": 2}}}
    }
  ]
}
```

There is no score anywhere. Pages sort by finding count, descending; a page that
cannot be parsed keeps a row with `error` and no findings, and a check that
raises is recorded in that page's `errors` array instead of failing the run.

### Checks

| Check | Reports |
|---|---|
| `contrast` | text below the WCAG floor **on a background the page can resolve** |
| `off_canvas` | an element whose box extends more than 1px past the canvas |

`contrast` resolves the paint directly behind each text box: a flat fill that
covers it wins, otherwise the page background applies. When the cover is an
`image`, a gradient or pattern, an unresolved paint, a translucent layer, or
nothing flat at all, the run is **skipped and counted in `detail.skipped`** --
a photograph has no single luminance, so comparing against it would invent a
failure. That is the difference between a fact and a false alarm.

Design is not checked. A deliberate low-contrast cover is reported as a fact and
left alone; the report says what is there, not what should be.

## `page_patch.py`

```bash
python3 scripts/page_patch.py P07.svg --set "title:fill=#FFFFFF"
python3 scripts/page_patch.py P07.svg --id title-bg --grow-left 20%
python3 scripts/page_patch.py P07.svg --id card --pad 12 --dry-run
python3 scripts/page_patch.py P07.svg --id title --center-x page
python3 scripts/page_patch.py P07.svg --measure title
```

`--set` takes `<id>:<attribute>=<value>`. The operators take a length (px) or a
percentage of the element's current width/height, and act on `--id`, or on an
inline `<id>:<value>`:

| Operator | Effect |
|---|---|
| `--grow-left V` | `x -= V`, `width += V` (percentage uses the current width) |
| `--grow-right V` | `width += V` |
| `--grow-top V` | `y -= V`, `height += V` (percentage uses the current height) |
| `--grow-bottom V` | `height += V` |
| `--pad V` | all four sides by `V` px |
| `--center-x page\|ID` | centre horizontally on the canvas or another element |
| `--center-y page\|ID` | centre vertically |
| `--measure ID` | print x/y/width/height in px and EMU, and a text element's measured width |

Operators and `--set` apply in the order they are written, so
`--set "card:fill=#000" --grow-left 50` changes the colour first, then the box.
Only the matched attribute substrings are rewritten: comments, attribute order,
and every untouched byte survive.

| Exit | Meaning |
|---|---|
| `0` | applied, measured, shown, or dry-run printed |
| `2` | missing page, no operation, unknown/duplicate id, unreadable length, missing target |
| `3` | the patch would produce invalid XML, or the attribute name is not a valid XML name |

## `page_zoom.py`

```bash
python3 scripts/page_zoom.py P07.svg --id title-bg --scale 4 --out zoom.png
python3 scripts/page_zoom.py before.svg --id title-bg --compare after.svg --out cmp.png
```

Renders the page with headless Chrome at `--scale`, crops to the element's box
plus `--margin` (default 24px), and writes a PNG. `--compare` renders a second
page, crops the same rectangle, and places the two side by side with BEFORE and
AFTER headers and an 8px gap.

Chrome is located at `C:/Program Files/Google/Chrome/Application/chrome.exe`,
the x86 equivalent, or `%LOCALAPPDATA%`. Set `PPT_MASTER_CHROME` to override.
A missing browser exits `2` with a message, never a traceback.

## The loop

1. `page_check.py <project>` -- read the findings, not a score.
2. `page_patch.py` -- change the one attribute or edge the finding names.
3. `page_zoom.py --compare` -- look at the result enlarged.
4. `svg_quality_checker.py` before export, as always; the checks never replace it.
