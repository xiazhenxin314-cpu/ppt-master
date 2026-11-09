# Aesthetic Audit and Page Patch

Tool documentation for `scripts/aesthetic_audit.py` and `scripts/page_patch.py`.
Both commands run from the skill root, both are opt-in, and neither changes what
`svg_to_pptx.py` exports.

Dependencies: standard library plus sibling modules — `slide_roster`,
`console_encoding`, `attribution_guard`, and the `aesthetic_quality` package.

## Quick reference

| Task | Command | Output |
|---|---|---|
| Score every page | `python3 scripts/aesthetic_audit.py <project_path>` | ranked table on stdout |
| Write the report | `python3 scripts/aesthetic_audit.py <project_path> --json validation/aesthetic_audit.json` | JSON file plus one `report: <path>` line on stderr |
| List metric ids | `python3 scripts/aesthetic_audit.py --list` | one line per registered metric |
| Change one attribute | `python3 scripts/page_patch.py svg_output/P07.svg --set "P07-title:font-size=44"` | `<id>.<attr>: <old> -> <new>` on stdout |
| Preview a change | `python3 scripts/page_patch.py svg_output/P07.svg --set "P07-title:font-size=44" --dry-run` | the same lines, file untouched |
| Print one element | `python3 scripts/page_patch.py svg_output/P07.svg --show P07-title` | that element's start tag |

Scope against `visual-review`: that stage renders every page to PNG and has a
subagent read the images, about 100–150K tokens for a 20-page deck. This pair
walks bounding boxes and colour values only — zero tokens, seconds per deck — and
it names the page and the attribute to change. It does not replace a render pass.

## `aesthetic_audit.py`

```bash
python3 scripts/aesthetic_audit.py projects/demo
python3 scripts/aesthetic_audit.py projects/demo --json validation/aesthetic_audit.json
python3 scripts/aesthetic_audit.py projects/demo --only contrast,grid_deviation
python3 scripts/aesthetic_audit.py svg_output/P07.svg --grid 8 --min-contrast 4.5
python3 scripts/aesthetic_audit.py --list
```

Page discovery: `<project_path>/svg_output/` when that directory exists, otherwise
the directory or single file given as the target, ordered by
`slide_roster.discover_slide_svgs`.

| Flag | Meaning |
|---|---|
| `--json PATH` | write the full report; parent directories are created |
| `--only IDS` | comma-separated metric ids |
| `--grid N` | layout grid step in px; omit to skip the grid metric |
| `--min-contrast N` | WCAG text threshold, default `4.5`; large text (≥24px, or ≥18.66px bold) uses `3.0` |
| `--top N` | rows printed to stdout, default `10` |
| `--list` | print registered metric ids and summaries, then exit `0` |

Exit codes: `0` table printed; `2` no target argument, no SVG page under it, or
`--only` naming no registered metric.

### Report schema

```json
{
  "schema": "ppt-master.aesthetic-audit.v1",
  "metrics": ["contrast", "grid_deviation"],
  "pages": [
    {
      "page": "P07",
      "score": 0.42,
      "scores": {"contrast": 0.0, "grid_deviation": 0.42},
      "findings": [{"id": "P07-card", "tag": "rect", "deviation": 3.2, "metric": "grid_deviation"}],
      "errors": [{"metric": "contrast", "error": "..."}],
      "canvas": [1280, 720]
    }
  ]
}
```

`score` is the maximum of that page's metric scores, never an average: one
clearly broken property is the repair target, and an average buries it.

A page whose metric raised, or returned a non-finite score, carries
`"status": "error"`, appends `{"metric": ..., "error": ...}` to `errors`, and
sorts **before every scored page**. That ordering is deliberate: a failed metric
is not a clean page, and a plain `0.0` would sink it to the bottom of the repair
queue. `status` and `errors` are absent when every metric ran. The audit always
finishes.

### Built-in metrics

| Metric | Baseline | What it flags |
|---|---|---|
| `contrast` | WCAG floor (`--min-contrast`, large text `3.0`) | text runs below the floor against the page background; skipped when the page has no flat background |
| `grid_deviation` | `--grid N`; skipped when the flag is omitted | authored anchors off the grid: `rect` and `image` on both axes, `text` on x only (its y is a baseline and its right edge an estimate) |

The grid step is declared on the command line, not read from the page or the
lock. Reading a page-level grid is a future contract; it is not current behavior.

### Metric contract

A metric is one registered function; the harness owns parsing, ranking, and
reporting.

```python
@metric('whitespace_ratio', 'Share of the canvas left empty')
def _whitespace(page: Page, config: dict) -> MetricResult:
    drawn = sum((n.bbox[2] - n.bbox[0]) * (n.bbox[3] - n.bbox[1]) for n in page.shapes())
    ratio = max(0.0, 1.0 - drawn / (page.width * page.height))
    target = float(config.get('target_whitespace', 0.28))
    return MetricResult(score=max(0.0, (target - ratio) / target), detail={'ratio': ratio})
```

Returning anything else — a `dict`, a bare float — is rejected with a `TypeError`
that names the metric, instead of failing later on `.score`.

1. **A declared number, not a taste.** The score compares against a value the
   lock, the template, or the run already states. A metric with no reference
   number belongs in review, not here.
2. **`0.0` is clean and `1.0` is worst**; scores are clamped to that range. A
   non-finite score is a fault, recorded in `errors`, never read as clean.
3. **Findings carry the node `id`**, which is the argument `page_patch.py` takes.
4. **Name the exception classes you expect.** An unlisted failure is a bug and
   still raises.

### Geometry limits

Bounding boxes come from `rect`, `circle`, `ellipse`, `line`, `polygon`,
`polyline`, `image`, and `text`. `path` contributes no box. A transform is applied
to all four corners and reported as the smallest upright box containing them, so
a rotated shape's box is larger than its unrotated size. A text box is measured from its
anchor, font size, and glyph count — so its right edge is an estimate, not an
authored length. The audit is a triage signal; a render remains the authority on
how a page looks.

## `page_patch.py`

```bash
python3 scripts/page_patch.py svg_output/P07.svg --set "P07-title:font-size=44"
python3 scripts/page_patch.py svg_output/P07.svg --set "bg:fill=#0F172A" --dry-run
python3 scripts/page_patch.py svg_output/P07.svg --set "a:x=1" --set "b:fill=#fff" --out /tmp/P07.svg
python3 scripts/page_patch.py svg_output/P07.svg --show P07-title
```

`--set` takes `<element-id>:<attribute>=<value>` and repeats. The tool rewrites
the attribute substring only, so formatting, comments, attribute order, sibling
nodes, and every untouched byte survive — the file stays a minimal diff. A value
that already exists is replaced; a missing attribute is inserted into the start
tag; XML special characters are escaped.

| Flag | Meaning |
|---|---|
| `--dry-run` | compute and print the changes, write nothing |
| `--out PATH` | write elsewhere instead of in place |
| `--show ID` | print one element's start tag, then exit |

Change lines go to stdout; `dry-run: nothing written` and `wrote <path>` go to
stderr, so piping stdout yields only the diff.

| Exit | Meaning |
|---|---|
| `0` | applied, or dry-run printed, or `--show` printed |
| `2` | page path missing; no `--set` given; unknown or non-unique element id; malformed `--set`; `--out` parent missing |
| `3` | the patch would produce invalid XML, or the attribute name is not a valid XML name; nothing written |

## Repair loop

1. `python3 scripts/aesthetic_audit.py <project_path> --json validation/aesthetic_audit.json`
2. Take the first page in the report and its worst metric's finding `id`.
3. `python3 scripts/page_patch.py svg_output/<page>.svg --set "<id>:<attr>=<value>"`
4. Re-run step 1; the score must drop, otherwise revert the patch.
5. `python3 scripts/svg_quality_checker.py <project_path> --canonical-authoring --stage final --json`

Step 5 stays mandatory: the audit never replaces the checker, and a coordinate
change that satisfies a metric can still break a text-width assertion.
