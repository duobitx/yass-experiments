# yass-report

Turns the per-run bundles produced by [`yass-export`](../yass-export/) into
human- and machine-readable deliverables: a written abstract, charts
(matplotlib **and** gnuplot), an interactive HTML page, a raw-data
spreadsheet, and a cross-run summary.

## Purpose

For each `*.tar.gz` bundle under a runs directory it generates a per-run
report; across all bundles it generates a summary. It is the offline
counterpart to the Grafana dashboards — it works straight from the exported
CSVs, no live cluster needed.

## Requirements

- A Python venv with `matplotlib`, `openpyxl`, `pyyaml`. The wrapper expects
  it at `.tools/reportvenv` (repo root). Create it once:
  ```sh
  python3 -m venv .tools/reportvenv
  .tools/reportvenv/bin/pip install matplotlib openpyxl pyyaml
  ```
- `gnuplot` on `$PATH` — **optional**; only needed to render the gnuplot
  `.gnuplot` scripts to PNG. Without it the scripts + `.dat` are still
  emitted (render them later anywhere gnuplot is installed).
- Input bundles from `yass-export` (a `metrics-csv/`, `events-csv/` and —
  for newer bundles — a `manifests/` directory). Works with the `.tar.gz`
  or the uncompressed bundle dir.

## Usage

```sh
# default: read ./_runs/*.tar.gz, write ./_runs/report/
tools/yass-report/yass-report.sh experiments/uc1-rapid-disaster-response/_runs

# explicit out dir / UC label
tools/yass-report/yass-report.sh <runs_dir> --out <dir> --uc uc1
```

The wrapper (`yass-report.sh`) just runs `yass-report.py` under the venv.
The output directory is **wiped on every run — only the latest report is
kept** (by design).

## Outputs (`<runs_dir>/report/`)

Per run (`report/<run_id>/`):
- `README.md` — written abstract: Setup / Parameters / Result / Observations
  (auto) / Conclusions (fill in) / KPI verdict.
- `index.html` — interactive page (Chart.js: per-target delivery + network
  by node) + KPI table + embedded PNGs + links. Open in a browser.
- `charts/*.png` — matplotlib charts.
- `gnuplot/*.{gnuplot,dat,png}` — gnuplot scripts, data, and (if gnuplot is
  installed) rendered PNGs.
- `raw.xlsx` — every metrics/events CSV as a sheet, plus a KPIs sheet.
- `metrics.json` — headline KPIs as JSON.

Cross-run (`report/`):
- `SUMMARY.md` — table of all runs + auto EDFS-vs-TUS conclusions.
- `index.html` — links to every per-run page + the cross-run charts.
- `charts/`, `gnuplot/` — `delivery_vs_satcount` and `cost_vs_delivery`.

## Viewing

Open `report/index.html` (or a per-run `report/<run_id>/index.html`) in a
browser. The interactive charts load Chart.js from a CDN (needs internet);
the embedded matplotlib/gnuplot PNGs render offline regardless.

## Caveats

- **EDFS delivery-time coverage** depends on the metrics-bridge
  `DELIVERY_DEADLINE` being ≥ the experiment's `maxDuration` (the operator
  now sets it to `maxDuration × 1.1` per experiment). Older runs may show
  `n/a` first-GS-delivery for slow EDFS variants.
- **`is_target_gs` is unreliable** — ground deliveries are identified by
  `target_fsNode` starting with `estrack`, not by that label.
- Single run per configuration → no statistics; treat per-run numbers as
  indicative. See the per-UC `_runs/ANALYSIS.md` for the fuller reading.

## Related tools

- [`yass-export`](../yass-export/) — produces the input bundles.
- [`yass-compare`](../yass-compare/) — paired TUS-vs-EDFS markdown report
  from two run_ids (complementary; this tool is whole-sweep oriented).
