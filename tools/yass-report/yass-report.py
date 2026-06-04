#!/usr/bin/env python3
"""yass-report — turn yass-export bundles into a publishable multi-UC HTML site.

Usage:
    yass-report.py [experiments_dir] [--out DIR] [--only ucN]

Discovers `uc<N>-…/` experiment directories under experiments_dir (default
`<repo>/yass-experiments/experiments`), reads every `_runs/*.tar.gz` bundle in
each, and writes a static site under <out> (default `<experiments_dir>/../results`):

    results/index.html              — landing page, all UCs + descriptions
    results/assets/site.css         — shared stylesheet
    results/ucN/index.html          — UC description (from README) + variants table
    results/ucN/<variant>/index.html — per-run KPIs + charts + raw files

The output dir is wiped on each run. Charts are Chart.js (interactive,
stacked full-width) plus matplotlib/gnuplot PNGs. Raw data (raw.xlsx, CSVs)
is kept gzipped per variant and is NOT meant for publishing.
"""
import sys, os, csv, re, json, glob, gzip, tarfile, tempfile, shutil, subprocess, argparse
from datetime import datetime
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Dark palette matching the interactive (Chart.js) charts so PNGs blend in.
PANEL, GRID, FG = "#171a21", "#2a2f3a", "#cfd6e4"
C_BLUE, C_GREY, C_GREEN, C_RED = "#4c78a8", "#888888", "#54a24b", "#e45756"
plt.rcParams.update({
    "figure.facecolor": PANEL, "axes.facecolor": PANEL, "savefig.facecolor": PANEL,
    "text.color": FG, "axes.labelcolor": FG, "axes.titlecolor": FG,
    "axes.edgecolor": GRID, "xtick.color": FG, "ytick.color": FG, "grid.color": GRID,
})
from openpyxl import Workbook
import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

NRT_2H, NRT_4H = 7200.0, 14400.0
MiB = 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates")

# KPI metadata: key -> (unit, description). Superset across all UCs.
KPI_META = {
    "engine": ("—", "Storage/transport engine under test (edfs or tus)"),
    "state": ("—", "Terminal experiment state (Success/TimedOut/Failure)"),
    "exec_date": ("UTC", "Wall-clock date/time the experiment was launched (CR creationTimestamp)"),
    "duration": ("—", "Wall-clock experiment duration (creation → last reconcile transition)"),
    "sim_start": ("UTC", "Simulation clock at experiment start (simulationStartTime)"),
    "sim_end": ("UTC", "Simulation clock when the experiment finished (experimentTime)"),
    "file_size": ("—", "Size of the produced photo file"),
    "priority": ("—", "File priority class (low/default/high; EDFS PAR)"),
    "sat_count": ("sats", "Number of satellites in the constellation"),
    "RF": ("copies", "EDFS replication factor (n/a for TUS)"),
    "first_GS_delivery_s": ("s", "Time from sim start until the first produced file reached any ground station"),
    "last_GS_delivery_s": ("s", "Time from sim start until the last produced file reached a ground station (all-delivered time)"),
    "files_to_GS": ("count", "Distinct produced files that reached at least one ground station"),
    "all_delivered": ("—", "Whether every produced file reached a ground station"),
    "delivered": ("—", "Whether the photo reached any ground station (UC4 success criterion)"),
    "GS_with_delivery": ("count", "Ground stations with a recorded delivery time"),
    "GS_reached": ("count", "Ground stations that received the file (from received_total — independent of the delivery-time metric)"),
    "distinct_receivers": ("count", "Distinct nodes that received a file (GS + relays)"),
    "produced": ("files", "Files produced by the producer agent(s)"),
    "TX_MiB": ("MiB", "Total network bytes transmitted, all nodes"),
    "RX_MiB": ("MiB", "Total network bytes received, all nodes"),
    "peak_cpu_m": ("millicores", "Peak CPU across all containers"),
    "peak_mem_MiB": ("MiB", "Peak memory across all containers"),
}
# Secondary KPIs shown for every UC (resource cost + reach).
_TAIL = ["GS_reached", "distinct_receivers", "TX_MiB", "RX_MiB", "peak_cpu_m", "peak_mem_MiB"]

# Per-UC configuration: headline metric (for cross-run charts/conclusions),
# the KPI rows on the variant page, and the variant-table columns (excl. state,
# which the template appends). Column entries are (header, k-field).
UC_CFG = {
    "uc1": dict(headline="first_gs", hlabel="time-to-first-GS-delivery (s)",
        kpis=["engine", "state", "file_size", "priority", "sat_count", "RF",
              "first_GS_delivery_s", "GS_with_delivery", "produced"] + _TAIL,
        cols=[("engine", "engine"), ("size", "file_size"), ("priority", "priority"),
              ("sat", "sat_count"), ("RF", "rf"), ("firstGS(s)", "first_gs")]),
    "uc2": dict(headline="last_gs", hlabel="time-until-all-files-delivered (s)",
        kpis=["engine", "state", "priority", "RF", "sat_count", "produced",
              "files_to_GS", "all_delivered", "first_GS_delivery_s",
              "last_GS_delivery_s"] + _TAIL,
        cols=[("engine", "engine"), ("priority", "priority"), ("RF", "rf"),
              ("sat", "sat_count"), ("produced", "produced"),
              ("files→GS", "files_to_gs"), ("all?", "all_delivered"),
              ("lastGS(s)", "last_gs")]),
    "uc3": dict(headline="first_gs", hlabel="time-to-first-GS-delivery (s)",
        kpis=["engine", "state", "file_size", "priority", "sat_count", "RF",
              "first_GS_delivery_s", "GS_with_delivery", "produced"] + _TAIL,
        cols=[("engine", "engine"), ("size", "file_size"), ("priority", "priority"),
              ("sat", "sat_count"), ("RF", "rf"), ("firstGS(s)", "first_gs")]),
    "uc4": dict(headline="first_gs", hlabel="time-to-first-GS-delivery (s)",
        kpis=["engine", "state", "priority", "sat_count", "delivered",
              "first_GS_delivery_s", "produced"] + _TAIL,
        cols=[("engine", "engine"), ("priority", "priority"), ("sat", "sat_count"),
              ("delivered", "delivered"), ("firstGS(s)", "first_gs")]),
    "uc5": dict(headline="last_gs", hlabel="time-until-all-files-delivered (s)",
        kpis=["engine", "state", "sat_count", "RF", "produced", "files_to_GS",
              "all_delivered", "first_GS_delivery_s", "last_GS_delivery_s"] + _TAIL,
        cols=[("engine", "engine"), ("sat", "sat_count"), ("RF", "rf"),
              ("produced", "produced"), ("files→GS", "files_to_gs"),
              ("all?", "all_delivered"), ("lastGS(s)", "last_gs")]),
}

# README sections to drop from the published UC description (operational).
DOC_DENY = ("running", "inputs", "regenerating", "sat selection", "run-id", "run id")

# ---------------- parsing ----------------

def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_date(s):
    d = parse_ts(s)
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "n/a"


def fmt_dur(sec):
    if sec is None:
        return "n/a"
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


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

    dsum = {(l.get("source_fsNode"), l.get("target_fsNode")): v for l, v, _ in read_metric(m("yass_file_delivery_seconds_sum"))}
    dcnt = {(l.get("source_fsNode"), l.get("target_fsNode")): v for l, v, _ in read_metric(m("yass_file_delivery_seconds_count"))}
    per_t = {k: dsum[k] / dcnt[k] for k in dsum if dcnt.get(k, 0) > 0}
    gs = {k: v for k, v in per_t.items() if (k[1] or "").startswith("estrack")}

    recv = {l.get("fsNode") for l, _, _ in read_metric(m("yass_file_received_total"))}
    gs_reached = len({n for n in recv if (n or "").startswith("estrack")})
    tx = sum(v for _, v, _ in read_metric(m("yass_network_tx_bytes_total")))
    rx = sum(v for _, v, _ in read_metric(m("yass_network_rx_bytes_total")))
    cpu = read_metric(m("yass_container_cpu_millicores"))
    mem = read_metric(m("yass_container_memory_bytes"))
    produced = sum(v for _, v, _ in read_metric(m("yass_file_produced_total")))

    exp = (load_yaml(os.path.join(bundle, "manifests", "experiment.yaml"))
           or load_yaml(os.path.join(bundle, "experiment.yaml")) or {})
    status = exp.get("status") or {}
    state = status.get("experimentState") or "?"
    spec = exp.get("spec") or {}

    # Wall-clock execution date + duration: CR creation → last reconcile transition.
    created = (exp.get("metadata") or {}).get("creationTimestamp")
    c0 = parse_ts(created)
    ends = [t for t in (parse_ts(c.get("lastTransitionTime"))
                        for c in (status.get("conditions") or [])) if t]
    c1 = max(ends) if ends else None
    duration_s = (c1 - c0).total_seconds() if (c0 and c1) else None

    # Per-producer first-GS delivery: each producing sat's earliest landing on
    # any GS. first_gs = first file to land; last_gs = when the last one landed
    # (= "all delivered" time for UC2/UC5). files_to_gs = produced files that
    # reached a GS; all_delivered when that covers everything produced.
    by_prod = {}
    for (src, tgt), v in gs.items():
        by_prod.setdefault(src, []).append(v)
    producer_first = {s: min(vs) for s, vs in by_prod.items()}
    files_to_gs = len(producer_first)
    last_gs = max(producer_first.values()) if producer_first else None

    return dict(
        run_id=rid, **info,
        state=state,
        exec_date=created, duration_s=duration_s,
        sim_start=spec.get("simulationStartTime"),
        sim_end=status.get("experimentTime"),
        first_gs=min(gs.values()) if gs else None,
        last_gs=last_gs,
        mean_gs=(sum(gs.values()) / len(gs)) if gs else None,
        n_gs=len(gs),
        gs_reached=gs_reached,
        files_to_gs=files_to_gs,
        all_delivered=bool(produced) and files_to_gs >= round(produced),
        delivered=files_to_gs >= 1,
        n_receivers=len([r for r in recv if r]),
        per_target=per_t,
        gs_targets=gs,
        produced=produced,
        tx_mib=tx / MiB, rx_mib=rx / MiB,
        peak_cpu=max([p for _, _, p in cpu], default=0.0),
        peak_mem_mib=max([p for _, _, p in mem], default=0.0) / MiB,
    )


def kpi_value(k, key):
    fg = f"{k['first_gs']:.0f}" if k["first_gs"] is not None else "n/a"
    lg = f"{k['last_gs']:.0f}" if k["last_gs"] is not None else "n/a"
    return {
        "engine": k["engine"], "state": k["state"],
        "exec_date": fmt_date(k["exec_date"]), "duration": fmt_dur(k["duration_s"]),
        "sim_start": fmt_date(k["sim_start"]), "sim_end": fmt_date(k["sim_end"]),
        "file_size": k["file_size"],
        "priority": k["priority"], "sat_count": k["sat_count"],
        "RF": k["rf"] if k["rf"] is not None else "n/a",
        "first_GS_delivery_s": fg, "last_GS_delivery_s": lg,
        "files_to_GS": k["files_to_gs"],
        "all_delivered": "yes" if k["all_delivered"] else "no",
        "delivered": "yes" if k["delivered"] else "no",
        "GS_with_delivery": k["n_gs"],
        "GS_reached": k["gs_reached"], "distinct_receivers": k["n_receivers"],
        "produced": f"{k['produced']:.0f}", "TX_MiB": f"{k['tx_mib']:.0f}",
        "RX_MiB": f"{k['rx_mib']:.0f}", "peak_cpu_m": f"{k['peak_cpu']:.0f}",
        "peak_mem_MiB": f"{k['peak_mem_mib']:.0f}",
    }[key]


def col_val(k, field):
    """Display value for a variant-table column (field = a compute() key)."""
    if field == "rf":
        return k["rf"] if k["rf"] is not None else "—"
    if field in ("first_gs", "last_gs"):
        return f"{k[field]:.0f}" if k[field] is not None else "n/a"
    if field == "produced":
        return f"{k['produced']:.0f}"
    if field == "all_delivered":
        return "yes" if k["all_delivered"] else "no"
    if field == "delivered":
        return "yes" if k["delivered"] else "no"
    return k.get(field)

# ---------------- per-run charts / files ----------------

def chart_delivery(k, outpng):
    gs = k["gs_targets"]
    if not gs:
        return False
    items = sorted(gs.items(), key=lambda x: x[1])
    labels = [t for (_, t), _ in items]
    vals = [v for _, v in items]
    plt.figure(figsize=(9, max(4, len(labels) * 0.3)))
    plt.barh(labels, vals, color="#4c78a8")
    plt.xlabel("delivery time (s)")
    plt.title(f"{k['run_id']} — per-GS delivery")
    plt.tight_layout()
    plt.savefig(outpng, dpi=110)
    plt.close()
    return True


def chart_bar(pairs, ylabel, title, outpng, top=12):
    pairs = sorted(pairs, key=lambda x: x[1], reverse=True)[:top]
    if not pairs:
        return False
    plt.figure(figsize=(9, 4))
    plt.bar([p[0] for p in pairs], [p[1] for p in pairs], color="#54a24b")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(outpng, dpi=110)
    plt.close()
    return True


def write_xlsx(bundle, outxlsx):
    wb = Workbook()
    ws = wb.active
    ws.title = "_index"
    ws.append(["sheet", "source csv"])
    seen = set()
    for sub in ("metrics-csv", "events-csv"):
        for csvf in sorted(glob.glob(os.path.join(bundle, sub, "*.csv"))):
            name = os.path.splitext(os.path.basename(csvf))[0][:28]
            t = name
            n = 1
            while t in seen:
                t = f"{name[:26]}_{n}"; n += 1
            seen.add(t)
            ws.append([t, os.path.join(sub, os.path.basename(csvf))])
            sh = wb.create_sheet(t)
            with open(csvf) as f:
                for row in csv.reader(f):
                    sh.append(row)
    wb.save(outxlsx)


def gzip_file(path):
    with open(path, "rb") as fi, gzip.open(path + ".gz", "wb") as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(path)


def tar_csvs(bundle, outpath):
    has = False
    with tarfile.open(outpath, "w:gz") as t:
        for sub in ("metrics-csv", "events-csv"):
            d = os.path.join(bundle, sub)
            if os.path.isdir(d):
                t.add(d, arcname=sub); has = True
    if not has:
        os.remove(outpath)
    return has


MANIFEST_DESC = {
    "hardwaredefinitions.yaml": "HardwareDefinitions (oneweb / ground-station specs)",
    "layout.yaml": "Layout — constellation topology (FsNodes, orbits)",
    "experimentdefinition.yaml": "ExperimentDefinition — behaviours, agents, faults, maxDuration",
    "experiment.yaml": "Experiment — the run instance (engine, RF, simulationStartTime)",
}


def copy_resources(env, run_id, bundle, resdir):
    """Copy the bundle's K8s manifests into resdir + write a listing page."""
    man = os.path.join(bundle, "manifests")
    if not os.path.isdir(man):
        return False
    files = [f for f in sorted(os.listdir(man)) if f.endswith((".yaml", ".yml"))]
    if not files:
        return False
    os.makedirs(resdir, exist_ok=True)
    for f in files:
        shutil.copy(os.path.join(man, f), os.path.join(resdir, f))
    html = env.get_template("resources.html").render(
        title=f"{run_id} — resources", root="../../../",
        crumbs=[{"text": run_id.split("-")[0], "href": "../../index.html"},
                {"text": run_id, "href": "../index.html"}, {"text": "resources"}],
        run_id=run_id,
        files=[{"name": f, "desc": MANIFEST_DESC.get(f, "")} for f in files])
    open(os.path.join(resdir, "index.html"), "w").write(html)
    return True


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
            f"set terminal pngcairo size 1000,460 background rgb '{PANEL}'\n"
            "set output 'delivery.png'\n"
            "set style data histograms\nset style fill solid 0.8\n"
            f"set border lc rgb '{GRID}'\n"
            f"set ylabel 'delivery time (s)' textcolor rgb '{FG}'\n"
            f"set xtics rotate by -45 textcolor rgb '{FG}'\n"
            f"set ytics textcolor rgb '{FG}'\n"
            f"set title '{k['run_id']} — per-GS delivery' textcolor rgb '{FG}'\n"
            f"plot 'delivery.dat' using 2:xtic(1) lc rgb '{C_BLUE}' notitle\n")


def render_gnuplot(gdir):
    if not shutil.which("gnuplot"):
        return
    for g in sorted(glob.glob(os.path.join(gdir, "*.gnuplot"))):
        try:
            subprocess.run(["gnuplot", os.path.basename(g)], cwd=gdir, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def aggregate_net(bundle, name):
    agg = {}
    for l, v, _ in read_metric(os.path.join(bundle, "metrics-csv", name + ".csv")):
        node = l.get("fsNode") or l.get("peer_node") or l.get("peer") or "?"
        agg[node] = agg.get(node, 0) + v
    return agg

# ---------------- file-propagation graph ----------------

def read_events(path):
    """events-csv/<kind>.csv -> list of header-keyed dict rows."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def _node_kind(name):
    return "gs" if (name or "").startswith("estrack") else "sat"


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build_propagation(bundle):
    """Per-file transfer edges for the propagation graph.
    EDFS: events-csv/block_recv.csv (multi-source, per block, from the bitswap
    tracer). TUS / fallback: events-csv/file_delivered.csv (origin->target star).
    Returns a dict for the template, or None when there are no transfer events."""
    ev_dir = os.path.join(bundle, "events-csv")
    raw = []  # (t_epoch, file, from, to, bytes)
    for r in read_events(os.path.join(ev_dir, "block_recv.csv")):
        frm, to, f = r.get("from_fsNode"), r.get("to_fsNode"), r.get("file")
        if frm and to and f:
            raw.append((parse_ts(r.get("experimentTime")), f, frm, to, _to_float(r.get("size"))))
    used = "block_recv" if raw else "file_delivered"
    delivered = read_events(os.path.join(ev_dir, "file_delivered.csv"))
    if not raw:  # TUS, or EDFS bundle predating the tracer
        for r in delivered:
            frm, to, f = r.get("source"), r.get("target"), r.get("name")
            if frm and to and f:
                raw.append((parse_ts(r.get("experimentTime")), f, frm, to, _to_float(r.get("size"))))
    if not raw:
        return None

    producers = {r["source"] for r in delivered if r.get("source")}
    ts = [t for t, *_ in raw if t is not None]
    t0 = min(ts) if ts else None

    events, node_ids, files = [], set(), set()
    for t, f, frm, to, b in raw:
        rel = round((t - t0).total_seconds(), 1) if (t and t0) else 0.0
        events.append({"t": rel, "file": f, "from": frm, "to": to, "bytes": b})
        node_ids.update((frm, to)); files.add(f)

    nodes = [{"id": n, "kind": _node_kind(n), "producer": n in producers}
             for n in sorted(node_ids)]
    return {"source": used, "files": sorted(files), "nodes": nodes,
            "events": events, "tmax": max((e["t"] for e in events), default=0.0)}


def build_deliveries(bundle):
    """Per (file, receiver) delivery table from events-csv/file_delivered.csv:
    which file reached which fsNode and after how long from its creation, plus
    how many fsNodes hold the file (distinct receivers + the producer). Works for
    both engines. Returns a flat list of rows sorted by file then delivery time."""
    per_file = {}  # name -> {"holders": set, "rx": {receiver: seconds}}
    for r in read_events(os.path.join(bundle, "events-csv", "file_delivered.csv")):
        name, tgt, src = r.get("name"), r.get("target"), r.get("source")
        if not name or not tgt:
            continue
        d = per_file.setdefault(name, {"holders": set(), "rx": {}})
        d["holders"].add(tgt)
        if src:
            d["holders"].add(src)
        sec = _to_float(r.get("deliverySeconds"))
        # keep the earliest delivery if the same (file, receiver) repeats
        if tgt not in d["rx"] or sec < d["rx"][tgt]:
            d["rx"][tgt] = sec
    rows = []
    for name in sorted(per_file):
        d = per_file[name]
        holders = len(d["holders"])
        for tgt, sec in sorted(d["rx"].items(), key=lambda x: x[1]):
            rows.append({"file": name, "fsNode": tgt, "delivery_s": round(sec, 1),
                         "holders": holders, "is_gs": _node_kind(tgt) == "gs"})
    return rows

# ---------------- cross-run (per UC) ----------------

def cross_run(uc_id, rows, ucdir, cfg):
    """Write cross-run PNGs into ucdir/charts; return (charts, conclusions).
    Uses the UC's headline metric (first_gs or last_gs)."""
    cdir = os.path.join(ucdir, "charts"); os.makedirs(cdir, exist_ok=True)
    metric, label = cfg["headline"], cfg["hlabel"]
    val = lambda r: r.get(metric)
    charts = []

    groups = {}
    for r in rows:
        if val(r) is None:
            continue
        groups.setdefault((r["engine"], r["file_size"]), []).append((r["sat_count"], val(r)))
    if groups:
        plt.figure(figsize=(9, 5))
        for (eng, fs), pts in sorted(groups.items()):
            pts.sort()
            plt.plot([x for x, _ in pts], [y for _, y in pts], marker="o", label=f"{eng} {fs}")
        plt.xlabel("sat_count"); plt.ylabel(label)
        plt.title(f"{uc_id.upper()} — delivery time vs constellation size")
        plt.legend(); plt.grid(True, alpha=.3); plt.tight_layout()
        plt.savefig(os.path.join(cdir, "delivery_vs_satcount.png"), dpi=110); plt.close()
        charts.append({"src": "charts/delivery_vs_satcount.png",
                       "caption": f"{label} vs constellation size"})

    have_cost = False
    plt.figure(figsize=(9, 5))
    for eng, col in (("tus", "#e45756"), ("edfs", "#4c78a8")):
        xs = [r["tx_mib"] for r in rows if r["engine"] == eng and val(r) is not None]
        ys = [val(r) for r in rows if r["engine"] == eng and val(r) is not None]
        if xs:
            plt.scatter(xs, ys, label=eng, color=col); have_cost = True
    if have_cost:
        plt.xlabel("total network TX (MiB)"); plt.ylabel(label)
        plt.title(f"{uc_id.upper()} — cost vs delivery")
        plt.legend(); plt.grid(True, alpha=.3); plt.tight_layout()
        plt.savefig(os.path.join(cdir, "cost_vs_delivery.png"), dpi=110); plt.close()
        charts.append({"src": "charts/cost_vs_delivery.png",
                       "caption": "Network cost vs delivery time (Pareto)"})
    else:
        plt.close()

    conclusions = []
    by = {("tus", r["file_size"], r["sat_count"]): r for r in rows if r["engine"] == "tus"}
    for r in sorted(rows, key=lambda x: x["sat_count"]):
        if r["engine"] != "edfs" or val(r) is None:
            continue
        t = by.get(("tus", r["file_size"], r["sat_count"]))
        if t and val(t):
            sp = val(t) / val(r)
            conclusions.append(f"n{r['sat_count']} {r['file_size']}: EDFS "
                               f"<b>{val(r):.0f}s</b> vs TUS <b>{val(t):.0f}s</b> "
                               f"→ <b>{sp:.1f}× {'faster' if sp > 1 else 'slower'}</b>")
    return charts, conclusions

# ---------------- README → description ----------------

def uc_description(readme_path):
    """Return (title, description_html, abstract_text) from a UC README,
    dropping operational sections."""
    title, abstract = "", ""
    if not os.path.exists(readme_path):
        return title, "", ""
    lines = open(readme_path).read().splitlines()
    kept, skip, in_abstract = [], False, False
    for line in lines:
        h1 = re.match(r"^#\s+(.+)", line)
        if h1:
            title = h1.group(1).strip()
            continue
        h2 = re.match(r"^##\s+(.+)", line)
        if h2:
            t = h2.group(1).lower()
            skip = any(d in t for d in DOC_DENY)
            in_abstract = "abstract" in t
            if skip:
                continue
        elif in_abstract and not abstract and line.strip() and not line.startswith("#"):
            abstract = re.sub(r"[*_`]", "", line.strip())
        if not skip:
            kept.append(line)
    html = markdown.markdown("\n".join(kept), extensions=["tables", "fenced_code"])
    return title, html, abstract

# ---------------- per-variant page ----------------

def variant_page(env, k, bundle, vdir, uc_id):
    os.makedirs(vdir, exist_ok=True)
    cfg = UC_CFG.get(uc_id, UC_CFG["uc1"])
    cdir = os.path.join(vdir, "charts"); os.makedirs(cdir, exist_ok=True)
    gdir = os.path.join(vdir, "gnuplot"); os.makedirs(gdir, exist_ok=True)

    chart_delivery(k, os.path.join(cdir, "delivery.png"))
    chart_bar([(l.get("peer_node") or l.get("peer") or l.get("fsNode") or "?", v)
               for l, v, _ in read_metric(os.path.join(bundle, "metrics-csv", "yass_network_tx_bytes_total.csv"))],
              "TX bytes", f"{k['run_id']} — network TX by node", os.path.join(cdir, "network_tx.png"))
    gnuplot_delivery(k, gdir); render_gnuplot(gdir)

    # raw files (kept local, gzipped)
    xlsx = os.path.join(vdir, "raw.xlsx")
    write_xlsx(bundle, xlsx); gzip_file(xlsx)
    has_csv = tar_csvs(bundle, os.path.join(vdir, "raw-csv.tar.gz"))
    jk = {x: k[x] for x in k if x not in ("per_target", "gs_targets")}
    open(os.path.join(vdir, "metrics.json"), "w").write(json.dumps(jk, indent=2))

    # kubernetes resources (CRs) to reproduce the run
    has_resources = copy_resources(env, k["run_id"], bundle, os.path.join(vdir, "resources"))

    # interactive chart data
    deliv = {}
    for (src, tgt), sec in sorted(k["per_target"].items(), key=lambda x: x[1]):
        deliv[tgt or "?"] = {"s": round(sec, 1), "gs": bool((tgt or "").startswith("estrack"))}
    tx = aggregate_net(bundle, "yass_network_tx_bytes_total")
    rx = aggregate_net(bundle, "yass_network_rx_bytes_total")
    nodes = sorted(set(tx) | set(rx), key=lambda n: tx.get(n, 0), reverse=True)[:12]
    data = {"delivery": deliv, "net": {"nodes": nodes,
            "tx": [round(tx.get(n, 0) / MiB, 1) for n in nodes],
            "rx": [round(rx.get(n, 0) / MiB, 1) for n in nodes]}}

    graph = build_propagation(bundle)
    deliveries = build_deliveries(bundle)

    deliv_horiz = len(deliv) > 18
    net_horiz = len(nodes) > 6
    pngs = []
    for sub in ("charts", "gnuplot"):
        for png in sorted(glob.glob(os.path.join(vdir, sub, "*.png"))):
            pngs.append({"src": os.path.relpath(png, vdir), "name": os.path.relpath(png, vdir)})

    kpi_keys = list(cfg["kpis"])
    # show right after engine, state (final order: exec_date, duration, sim_start, sim_end)
    for extra in ("sim_end", "sim_start", "duration", "exec_date"):
        if extra not in kpi_keys:
            kpi_keys.insert(2, extra)

    v = dict(
        run_id=k["run_id"], engine=k["engine"], state=k["state"],
        kpis=[{"key": key, "value": kpi_value(k, key),
               "unit": KPI_META[key][0], "desc": KPI_META[key][1]}
              for key in kpi_keys],
        data=json.dumps(data),
        deliv_h=max(220, 22 * len(deliv)) if deliv_horiz else 340,
        deliv_axis="y" if deliv_horiz else "x",
        deliv_valaxis="x" if deliv_horiz else "y",
        net_h=max(240, 26 * len(nodes)) if net_horiz else 340,
        net_axis="y" if net_horiz else "x",
        pngs=pngs, has_xlsx=True, has_csv=has_csv, has_resources=has_resources,
        has_graph=bool(graph), graph=json.dumps(graph) if graph else "null",
        deliveries=deliveries,
    )
    html = env.get_template("variant.html").render(
        title=f"{k['run_id']}", root="../../",
        crumbs=[{"text": k["run_id"].split("-")[0], "href": "../index.html"},
                {"text": k["run_id"]}],
        v=v)
    open(os.path.join(vdir, "index.html"), "w").write(html)

# ---------------- main ----------------

def process_uc(env, ucdir, outroot):
    uc_id = re.match(r"(uc\d+)", os.path.basename(ucdir)).group(1)
    cfg = UC_CFG.get(uc_id, UC_CFG["uc1"])
    bundles = sorted(glob.glob(os.path.join(ucdir, "_runs", "*.tar.gz")))
    if not bundles:
        return None
    ucout = os.path.join(outroot, uc_id); os.makedirs(ucout, exist_ok=True)

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
            variant_page(env, k, inner, os.path.join(ucout, rid), uc_id)
            print(f"  {uc_id} {rid}  state={k['state']}  firstGS={k['first_gs']}  lastGS={k['last_gs']}")

    title, desc_html, abstract = uc_description(os.path.join(ucdir, "README.md"))
    charts, conclusions = cross_run(uc_id, rows, ucout, cfg)

    # optional authored conclusions (UC level); rendered only if the file exists
    conclusions_md = ""
    cpath = os.path.join(ucdir, "CONCLUSIONS.md")
    if os.path.exists(cpath):
        txt = open(cpath).read().strip()
        if txt:
            conclusions_md = markdown.markdown(txt, extensions=["tables", "fenced_code"])

    variants = []
    for r in sorted(rows, key=lambda x: (x["engine"], x["sat_count"], x["priority"], x["rf"] or 0)):
        variants.append({"id": r["run_id"], "state": r["state"],
                         "cells": [col_val(r, field) for _, field in cfg["cols"]]})
    var_headers = ["variant"] + [h for h, _ in cfg["cols"]] + ["state"]

    uc = dict(id=uc_id, title=title or uc_id.upper(), description_html=desc_html,
              abstract=abstract, charts=charts, conclusions=conclusions,
              conclusions_md=conclusions_md,
              variants=variants, var_headers=var_headers)
    html = env.get_template("uc_index.html").render(
        title=uc["title"], root="../", crumbs=[{"text": uc["title"]}], uc=uc)
    open(os.path.join(ucout, "index.html"), "w").write(html)

    return dict(id=uc_id, title=uc["title"], abstract=abstract,
                variant_count=len(rows),
                engines=sorted({r["engine"] for r in rows}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("experiments_dir", nargs="?",
                    default=os.path.join(HERE, "..", "..", "experiments"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None, help="render a single UC, e.g. uc1")
    a = ap.parse_args()

    exp = os.path.abspath(a.experiments_dir)
    out = os.path.abspath(a.out or os.path.join(exp, "..", "results"))
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    shutil.copy(os.path.join(TPL, "site.css"), os.path.join(out, "assets", "site.css"))

    env = Environment(loader=FileSystemLoader(TPL),
                      autoescape=select_autoescape(["html"]))

    ucdirs = sorted(d for d in glob.glob(os.path.join(exp, "uc*"))
                    if os.path.isdir(d) and re.match(r"uc\d+", os.path.basename(d)))
    if a.only:
        ucdirs = [d for d in ucdirs if os.path.basename(d).startswith(a.only)]

    ucs = []
    for ucdir in ucdirs:
        meta = process_uc(env, ucdir, out)
        if meta:
            ucs.append(meta)

    html = env.get_template("landing.html").render(
        title="YASS experiment results", root="", crumbs=[], ucs=ucs)
    open(os.path.join(out, "index.html"), "w").write(html)

    if not ucs:
        print(f"no bundles found under {exp}/*/_runs", file=sys.stderr); sys.exit(1)
    print(f"\nwrote site for {len(ucs)} UC(s) → {out}")


if __name__ == "__main__":
    main()
