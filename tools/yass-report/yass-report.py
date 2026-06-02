#!/usr/bin/env python3
"""yass-report — turn yass-export bundles into per-run abstracts + charts +
raw spreadsheets, plus a cross-run summary.

Usage:
    yass-report.py [runs_dir] [--out DIR] [--uc UCN]

Reads every `*.tar.gz` bundle under runs_dir (default `_runs`), and writes a
report tree under <out> (default `<runs_dir>/report`). The output dir is wiped
on each run — only the latest report is kept.

Per run: README.md (Setup / Parameters / Result / Observations / Conclusions
template / KPI verdict), matplotlib PNG charts, gnuplot scripts + data, a raw
data spreadsheet (raw.xlsx, one sheet per CSV) and metrics.json.
Cross run: SUMMARY.md + delivery-vs-satcount and cost-vs-delivery charts
(matplotlib + gnuplot).
"""
import sys, os, csv, re, json, glob, tarfile, tempfile, shutil, subprocess, argparse
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import Workbook

NRT_2H, NRT_4H = 7200.0, 14400.0
MiB = 1024 * 1024

# ---------------- parsing ----------------

def read_metric(path):
    """[(labels:dict, final:float, peak:float)] from a prom-snapshot CSV."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        rd = csv.reader(f)
        hdr = next(rd, None)
        if not hdr:
            return out
        vstart = len(hdr)
        for i, h in enumerate(hdr):
            if re.match(r"\d{4}-\d\d-\d\dT", h):
                vstart = i
                break
        labidx = {h: i for i, h in enumerate(hdr[:vstart])}
        for row in rd:
            if not row:
                continue
            lab = {k: row[i] for k, i in labidx.items() if i < len(row)}
            vals = []
            for x in row[vstart:]:
                if x == "":
                    continue
                try:
                    vals.append(float(x))
                except ValueError:
                    pass
            if vals:
                out.append((lab, vals[-1], max(vals)))
    return out


def parse_run_id(rid):
    eng = "edfs" if "-edfs-" in rid else "tus"
    fs = re.search(r"-s(\d+[mg])-", rid)
    sat = re.search(r"-n(\d+)", rid)
    pr = re.search(r"-p(default|low|high)-", rid)
    rf = re.search(r"-rf(\d+)", rid)
    return dict(engine=eng,
                file_size=fs.group(1).upper() if fs else "?",
                sat_count=int(sat.group(1)) if sat else 0,
                priority=pr.group(1) if pr else ("-" if eng == "tus" else "default"),
                rf=int(rf.group(1)) if rf else None)


def load_yaml(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def compute(bundle, rid):
    info = parse_run_id(rid)
    m = lambda n: os.path.join(bundle, "metrics-csv", n + ".csv")

    # delivery per (source,target)
    dsum = {(l.get("source_fsNode"), l.get("target_fsNode")): v for l, v, _ in read_metric(m("yass_file_delivery_seconds_sum"))}
    dcnt = {(l.get("source_fsNode"), l.get("target_fsNode")): v for l, v, _ in read_metric(m("yass_file_delivery_seconds_count"))}
    per_t = {k: dsum[k] / dcnt[k] for k in dsum if dcnt.get(k, 0) > 0}
    gs = {k: v for k, v in per_t.items() if (k[1] or "").startswith("estrack")}

    recv = {l.get("fsNode") for l, _, _ in read_metric(m("yass_file_received_total"))}
    tx = sum(v for _, v, _ in read_metric(m("yass_network_tx_bytes_total")))
    rx = sum(v for _, v, _ in read_metric(m("yass_network_rx_bytes_total")))
    cpu = read_metric(m("yass_container_cpu_millicores"))
    mem = read_metric(m("yass_container_memory_bytes"))
    produced = sum(v for _, v, _ in read_metric(m("yass_file_produced_total")))

    exp = (load_yaml(os.path.join(bundle, "manifests", "experiment.yaml"))
           or load_yaml(os.path.join(bundle, "experiment.yaml")) or {})
    state = (((exp.get("status") or {}).get("experimentState")) or "?")
    spec = exp.get("spec") or {}

    return dict(
        run_id=rid, **info,
        state=state,
        sim_start=spec.get("simulationStartTime", "?"),
        first_gs=min(gs.values()) if gs else None,
        mean_gs=(sum(gs.values()) / len(gs)) if gs else None,
        n_gs=len(gs),
        n_receivers=len([r for r in recv if r]),
        per_target=per_t,
        gs_targets=gs,
        produced=produced,
        tx_mib=tx / MiB, rx_mib=rx / MiB,
        peak_cpu=max([p for _, _, p in cpu], default=0.0),
        peak_mem_mib=max([p for _, _, p in mem], default=0.0) / MiB,
    )

# ---------------- per-run outputs ----------------

def chart_delivery(k, outpng):
    gs = k["gs_targets"]
    if not gs:
        return False
    items = sorted(gs.items(), key=lambda x: x[1])
    labels = [t or "?" for (_, t), _ in [((s, t), v) for (s, t), v in items]]
    labels = [t for (_, t) in [kk for kk, _ in items]]
    vals = [v for _, v in items]
    plt.figure(figsize=(8, 4))
    plt.bar(labels, vals, color="#4c78a8")
    plt.ylabel("delivery time (s)")
    plt.title(f"{k['run_id']} — per-GS delivery")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(outpng, dpi=110)
    plt.close()
    return True


def chart_bar(pairs, ylabel, title, outpng, top=12):
    pairs = sorted(pairs, key=lambda x: x[1], reverse=True)[:top]
    if not pairs:
        return False
    plt.figure(figsize=(8, 4))
    plt.bar([p[0] for p in pairs], [p[1] for p in pairs], color="#54a24b")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(outpng, dpi=110)
    plt.close()
    return True


def write_xlsx(bundle, outxlsx, k):
    wb = Workbook()
    ws = wb.active
    ws.title = "KPIs"
    for i, (key, val) in enumerate(sorted(k.items()), 1):
        if key in ("per_target", "gs_targets"):
            continue
        ws.cell(row=i, column=1, value=key)
        ws.cell(row=i, column=2, value=str(val))
    seen = set()
    for sub in ("metrics-csv", "events-csv"):
        for csvf in sorted(glob.glob(os.path.join(bundle, sub, "*.csv"))):
            name = os.path.splitext(os.path.basename(csvf))[0][:28]
            t = name
            n = 1
            while t in seen:
                t = f"{name[:26]}_{n}"; n += 1
            seen.add(t)
            sh = wb.create_sheet(t)
            with open(csvf) as f:
                for row in csv.reader(f):
                    sh.append(row)
    wb.save(outxlsx)


def gnuplot_delivery(k, gdir):
    gs = sorted(k["gs_targets"].items(), key=lambda x: x[1])
    if not gs:
        return
    with open(os.path.join(gdir, "delivery.dat"), "w") as f:
        f.write("# target delivery_s\n")
        for (s, t), v in gs:
            f.write(f"{t} {v:.1f}\n")
    with open(os.path.join(gdir, "delivery.gnuplot"), "w") as f:
        f.write(
            "set terminal pngcairo size 900,420\n"
            "set output 'delivery.png'\n"
            "set style data histograms\nset style fill solid 0.8\n"
            "set ylabel 'delivery time (s)'\nset xtics rotate by -45\n"
            f"set title '{k['run_id']} — per-GS delivery'\n"
            "plot 'delivery.dat' using 2:xtic(1) notitle\n")


def render_gnuplot(gdir):
    """Render every *.gnuplot in gdir to PNG, if gnuplot is installed."""
    if not shutil.which("gnuplot"):
        return
    for g in sorted(glob.glob(os.path.join(gdir, "*.gnuplot"))):
        try:
            subprocess.run(["gnuplot", os.path.basename(g)], cwd=gdir, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def per_run_readme(k, has_charts):
    p = []
    p.append(f"# Run `{k['run_id']}`\n")
    p.append("## Setup\n")
    p.append(f"- Engine: **{k['engine']}**")
    p.append(f"- Terminal state: **{k['state']}**")
    p.append(f"- simulationStartTime: `{k['sim_start']}`")
    p.append(f"- Ground stations: 7 (ESTRACK)\n")
    p.append("## Parameters\n")
    p.append(f"- File size: **{k['file_size']}**")
    p.append(f"- Priority: **{k['priority']}**")
    p.append(f"- sat_count: **{k['sat_count']}**")
    p.append(f"- RF: **{k['rf'] if k['rf'] is not None else 'n/a (TUS)'}**\n")
    p.append("## Result (auto)\n")
    fg = f"{k['first_gs']:.0f} s" if k['first_gs'] is not None else "n/a (not recorded)"
    p.append(f"- Time-to-first-GS-delivery: **{fg}**")
    p.append(f"- GS that received (with delivery time): {k['n_gs']}")
    p.append(f"- Distinct receivers (incl. relays): {k['n_receivers']}")
    p.append(f"- Files produced: {k['produced']:.0f}")
    p.append(f"- Network TX / RX: {k['tx_mib']:.0f} / {k['rx_mib']:.0f} MiB")
    p.append(f"- Peak CPU / mem: {k['peak_cpu']:.0f} m / {k['peak_mem_mib']:.0f} MiB\n")
    p.append("## Observations (auto)\n")
    obs = []
    if k['state'] != "Success":
        obs.append(f"Experiment ended **{k['state']}** — check delivery.")
    if k['engine'] == "edfs" and k['first_gs'] is None and k['n_receivers'] > 0:
        obs.append("EDFS delivered to peers but no GS delivery_seconds recorded "
                   "(likely DeliveryDeadline eviction — see ANALYSIS).")
    if k['first_gs'] is not None:
        obs.append(f"Delivered to ground in {k['first_gs']:.0f} s "
                   f"({'within' if k['first_gs'] < NRT_2H else 'beyond'} the 2 h NRT target).")
    p.append("\n".join(f"- {o}" for o in obs) if obs else "- (none)")
    p.append("\n## Conclusions (fill in)\n")
    p.append("- _…_\n")
    p.append("## KPI verdict (auto)\n")
    if k['first_gs'] is None:
        p.append("- Delivery time: **n/a** (metric not captured).")
    else:
        p.append(f"- < 2 h NRT: {'✅' if k['first_gs'] < NRT_2H else '❌'}; "
                 f"< 4 h NRT: {'✅' if k['first_gs'] < NRT_4H else '❌'}")
    p.append("\n## Files\n")
    p.append("- `charts/` — matplotlib PNGs; `gnuplot/` — gnuplot scripts+data")
    p.append("- `raw.xlsx` — raw metrics & events (one sheet per CSV)")
    p.append("- `metrics.json` — KPIs as JSON\n")
    return "\n".join(p)

# ---------------- cross-run ----------------

def summary(rows, outdir):
    cdir = os.path.join(outdir, "charts"); os.makedirs(cdir, exist_ok=True)
    gdir = os.path.join(outdir, "gnuplot"); os.makedirs(gdir, exist_ok=True)

    # delivery vs sat_count, per (engine,file_size)
    plt.figure(figsize=(8, 5))
    groups = {}
    for r in rows:
        if r['first_gs'] is None:
            continue
        groups.setdefault((r['engine'], r['file_size']), []).append((r['sat_count'], r['first_gs']))
    series = []  # (datfile, title) per (engine,file_size) — one gnuplot line each
    for (eng, fs), pts in sorted(groups.items()):
        pts.sort()
        plt.plot([x for x, _ in pts], [y for _, y in pts], marker="o", label=f"{eng} {fs}")
        fn = f"delivery_{eng}_{fs}.dat"
        with open(os.path.join(gdir, fn), "w") as df:
            df.write("# sat first_gs_s\n")
            for s, y in pts:
                df.write(f"{s} {y:.1f}\n")
        series.append((fn, f"{eng} {fs}"))
    plt.xlabel("sat_count"); plt.ylabel("time-to-first-GS-delivery (s)")
    plt.title("UC1 — delivery time vs constellation size"); plt.legend(); plt.grid(True, alpha=.3)
    plt.tight_layout(); plt.savefig(os.path.join(cdir, "delivery_vs_satcount.png"), dpi=110); plt.close()

    # cost vs delivery (Pareto)
    plt.figure(figsize=(8, 5))
    for eng, col in (("tus", "#e45756"), ("edfs", "#4c78a8")):
        xs = [r['tx_mib'] for r in rows if r['engine'] == eng and r['first_gs'] is not None]
        ys = [r['first_gs'] for r in rows if r['engine'] == eng and r['first_gs'] is not None]
        if xs:
            plt.scatter(xs, ys, label=eng, color=col)
    plt.xlabel("total network TX (MiB)"); plt.ylabel("time-to-first-GS-delivery (s)")
    plt.title("UC1 — cost vs delivery"); plt.legend(); plt.grid(True, alpha=.3)
    plt.tight_layout(); plt.savefig(os.path.join(cdir, "cost_vs_delivery.png"), dpi=110); plt.close()

    plot_cmd = ("plot " + ", ".join(f"'{fn}' using 1:2 with linespoints title '{t}'"
                                    for fn, t in series)) if series else "plot 0 notitle"
    with open(os.path.join(gdir, "delivery_vs_satcount.gnuplot"), "w") as f:
        f.write("set terminal pngcairo size 900,520\nset output 'delivery_vs_satcount.png'\n"
                "set xlabel 'sat_count'\nset ylabel 'first-GS delivery (s)'\nset key outside\n"
                "set title 'UC1 delivery vs sat_count'\n"
                + plot_cmd + "\n")
    render_gnuplot(gdir)

    # SUMMARY.md
    s = ["# UC1 — cross-run summary\n", "## All runs\n",
         "| run_id | eng | sat | size | prio | RF | state | firstGS(s) | recv | TX MiB |",
         "|---|---|--:|--|--|--|--|--:|--:|--:|"]
    for r in sorted(rows, key=lambda x: x['run_id']):
        s.append(f"| {r['run_id']} | {r['engine']} | {r['sat_count']} | {r['file_size']} | "
                 f"{r['priority']} | {r['rf'] if r['rf'] is not None else '-'} | {r['state']} | "
                 f"{('%.0f'%r['first_gs']) if r['first_gs'] is not None else 'n/a'} | "
                 f"{r['n_receivers']} | {r['tx_mib']:.0f} |")
    s.append("\n## Charts\n- `charts/delivery_vs_satcount.png`\n- `charts/cost_vs_delivery.png`")
    # auto conclusions: compare edfs vs tus at matching points
    s.append("\n## Conclusions (auto)\n")
    auto = []
    by = {(r['engine'], r['file_size'], r['sat_count'], r['priority'], r['rf']): r for r in rows}
    for r in rows:
        if r['engine'] != 'edfs' or r['first_gs'] is None:
            continue
        t = by.get(('tus', r['file_size'], r['sat_count'], '-', None))
        if t and t['first_gs']:
            sp = t['first_gs'] / r['first_gs']
            auto.append(f"- n{r['sat_count']} {r['file_size']}: EDFS {r['first_gs']:.0f}s vs "
                        f"TUS {t['first_gs']:.0f}s → **{sp:.1f}× {'faster' if sp>1 else 'slower'}**")
    s.append("\n".join(auto) if auto else "- (no matched EDFS/TUS delivery pairs captured)")
    s.append("\n## Conclusions (fill in)\n- _…_\n")
    open(os.path.join(outdir, "SUMMARY.md"), "w").write("\n".join(s) + "\n")

# ---------------- HTML ----------------

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def aggregate_net(inner, name):
    agg = {}
    for l, v, _ in read_metric(os.path.join(inner, "metrics-csv", name + ".csv")):
        node = l.get("fsNode") or l.get("peer_node") or l.get("peer") or "?"
        agg[node] = agg.get(node, 0) + v
    return agg


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>UC1 run — __TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 .wrap{max-width:1000px;margin:0 auto;padding:24px}
 h1{font-size:20px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #2a2f3a;padding-bottom:4px}
 table{border-collapse:collapse;width:100%} td,th{padding:4px 10px;border-bottom:1px solid #222;text-align:left}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
 canvas{background:#171a21;border-radius:8px;padding:8px}
 .badge{display:inline-block;padding:2px 8px;border-radius:10px;background:#2a2f3a}
 a{color:#6db3ff} img{max-width:100%;border-radius:8px;background:#fff}
 code{background:#1c2027;padding:1px 5px;border-radius:4px}
</style></head><body><div class="wrap">
<h1>UC1 run <code>__TITLE__</code> <span class="badge">__ENGINE__</span> <span class="badge">__STATE__</span></h1>
<h2>KPIs</h2><table>__KPITABLE__</table>
<h2>Charts (interactive)</h2><div class="grid">
 <div><canvas id="cDeliv"></canvas></div>
 <div><canvas id="cNet"></canvas></div>
</div>
<h2>Charts (matplotlib / gnuplot PNG)</h2><div class="grid">__PNGS__</div>
<h2>Conclusions (fill in)</h2><p><em>…</em></p>
<h2>Files</h2><ul>
 <li><a href="README.md">README.md</a> — written abstract</li>
 <li><a href="raw.xlsx">raw.xlsx</a> — raw metrics &amp; events</li>
 <li><a href="metrics.json">metrics.json</a> — KPIs</li>
 <li><code>charts/</code> (matplotlib PNG), <code>gnuplot/</code> (gnuplot scripts+PNG)</li>
</ul>
<script>
const D = __DATA__;
const gridc = '#2a2f3a', txt = '#cfd6e4';
Chart.defaults.color = txt; Chart.defaults.borderColor = gridc;
const dk = Object.keys(D.delivery);
new Chart(cDeliv,{type:'bar',data:{labels:dk,
  datasets:[{label:'delivery (s)',data:dk.map(k=>D.delivery[k].s),
    backgroundColor:dk.map(k=>D.delivery[k].gs?'#4c78a8':'#888')}]},
  options:{plugins:{title:{display:true,text:'Per-target delivery (blue=GS)'}},
    scales:{y:{title:{display:true,text:'seconds'}}}}});
new Chart(cNet,{type:'bar',data:{labels:D.net.nodes,
  datasets:[{label:'TX MiB',data:D.net.tx,backgroundColor:'#54a24b'},
            {label:'RX MiB',data:D.net.rx,backgroundColor:'#e45756'}]},
  options:{plugins:{title:{display:true,text:'Network by node (MiB)'}}}});
</script></div></body></html>"""


def per_run_html(k, inner, rdir):
    deliv = {}
    for (src, tgt), sec in k["per_target"].items():
        deliv[tgt or "?"] = {"s": round(sec, 1), "gs": bool((tgt or "").startswith("estrack"))}
    tx = aggregate_net(inner, "yass_network_tx_bytes_total")
    rx = aggregate_net(inner, "yass_network_rx_bytes_total")
    nodes = sorted(set(tx) | set(rx), key=lambda n: tx.get(n, 0), reverse=True)[:12]
    data = {"delivery": deliv, "net": {"nodes": nodes,
            "tx": [round(tx.get(n, 0) / MiB, 1) for n in nodes],
            "rx": [round(rx.get(n, 0) / MiB, 1) for n in nodes]}}
    kpi = [("engine", k["engine"]), ("state", k["state"]), ("file_size", k["file_size"]),
           ("priority", k["priority"]), ("sat_count", k["sat_count"]), ("RF", k["rf"]),
           ("first_GS_delivery_s", f"{k['first_gs']:.0f}" if k["first_gs"] is not None else "n/a"),
           ("GS_with_delivery", k["n_gs"]), ("distinct_receivers", k["n_receivers"]),
           ("produced", f"{k['produced']:.0f}"), ("TX_MiB", f"{k['tx_mib']:.0f}"),
           ("RX_MiB", f"{k['rx_mib']:.0f}"), ("peak_cpu_m", f"{k['peak_cpu']:.0f}"),
           ("peak_mem_MiB", f"{k['peak_mem_mib']:.0f}")]
    kpitable = "".join(f"<tr><th>{_esc(a)}</th><td>{_esc(b)}</td></tr>" for a, b in kpi)
    pngs = ""
    for sub in ("charts", "gnuplot"):
        for png in sorted(glob.glob(os.path.join(rdir, sub, "*.png"))):
            rel = os.path.relpath(png, rdir)
            pngs += f'<div><img src="{rel}" alt="{_esc(rel)}"><div>{_esc(rel)}</div></div>'
    if not pngs:
        pngs = "<div><em>no PNG charts for this run</em></div>"
    html = (PAGE.replace("__TITLE__", _esc(k["run_id"])).replace("__ENGINE__", _esc(k["engine"]))
            .replace("__STATE__", _esc(k["state"])).replace("__KPITABLE__", kpitable)
            .replace("__PNGS__", pngs).replace("__DATA__", json.dumps(data)))
    open(os.path.join(rdir, "index.html"), "w").write(html)


def index_html(rows, out):
    body = ["<!doctype html><meta charset='utf-8'><title>UC1 report</title>",
            "<style>body{font:15px system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px}",
            "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}</style>",
            "<h1>UC1 — runs</h1>",
            "<p><a href='SUMMARY.md'>SUMMARY.md</a> · cross-run charts: ",
            "<a href='charts/delivery_vs_satcount.png'>delivery_vs_satcount</a>, ",
            "<a href='charts/cost_vs_delivery.png'>cost_vs_delivery</a></p>",
            "<table><tr><th>run</th><th>engine</th><th>sat</th><th>size</th><th>state</th><th>firstGS(s)</th></tr>"]
    for r in sorted(rows, key=lambda x: x["run_id"]):
        fg = f"{r['first_gs']:.0f}" if r["first_gs"] is not None else "n/a"
        body.append(f"<tr><td><a href='{r['run_id']}/index.html'>{r['run_id']}</a></td>"
                    f"<td>{r['engine']}</td><td>{r['sat_count']}</td><td>{r['file_size']}</td>"
                    f"<td>{r['state']}</td><td>{fg}</td></tr>")
    body.append("</table>")
    open(os.path.join(out, "index.html"), "w").write("\n".join(body))


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_dir", nargs="?", default="_runs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--uc", default="uc1")
    a = ap.parse_args()
    runs = os.path.abspath(a.runs_dir)
    out = a.out or os.path.join(runs, "report")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    bundles = sorted(glob.glob(os.path.join(runs, "*.tar.gz")))
    if not bundles:
        print(f"no bundles in {runs}", file=sys.stderr); sys.exit(1)

    rows = []
    for tb in bundles:
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(tb) as t:
                t.extractall(tmp, filter="data")
            inner = next((os.path.join(tmp, d) for d in os.listdir(tmp)
                          if os.path.isdir(os.path.join(tmp, d))), None)
            if not inner:
                continue
            rid = os.path.basename(tb).replace(".tar.gz", "")
            half = len(rid) // 2
            if rid[:half] == rid[half + 1:]:
                rid = rid[:half]
            k = compute(inner, rid)
            rows.append(k)
            rdir = os.path.join(out, rid)
            cdir = os.path.join(rdir, "charts"); os.makedirs(cdir, exist_ok=True)
            gdir = os.path.join(rdir, "gnuplot"); os.makedirs(gdir, exist_ok=True)
            chart_delivery(k, os.path.join(cdir, "delivery.png"))
            chart_bar([(l.get("peer_node") or l.get("peer") or "?", v)
                       for l, v, _ in read_metric(os.path.join(inner, "metrics-csv", "yass_network_tx_bytes_total.csv"))],
                      "TX bytes", f"{rid} — network TX by peer", os.path.join(cdir, "network_tx.png"))
            gnuplot_delivery(k, gdir)
            render_gnuplot(gdir)
            write_xlsx(inner, os.path.join(rdir, "raw.xlsx"), k)
            jk = {x: k[x] for x in k if x not in ("per_target", "gs_targets")}
            open(os.path.join(rdir, "metrics.json"), "w").write(json.dumps(jk, indent=2))
            open(os.path.join(rdir, "README.md"), "w").write(per_run_readme(k, True))
            per_run_html(k, inner, rdir)
            print(f"  report: {rid}  first_gs={k['first_gs']}  state={k['state']}")

    summary(rows, out)
    index_html(rows, out)
    print(f"\nwrote {len(rows)} per-run reports + SUMMARY.md + index.html under {out}")


if __name__ == "__main__":
    main()
