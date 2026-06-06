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
from datetime import datetime, timedelta
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Dark palette matching the interactive (Chart.js) charts so PNGs blend in.
PANEL, GRID, FG = "#171a21", "#2a2f3a", "#cfd6e4"
C_BLUE, C_GREY, C_GREEN, C_RED = "#4c78a8", "#888888", "#54a24b", "#e45756"

# Canonical palette — the SAME concept gets the SAME colour on EVERY chart (PNG,
# Chart.js and the vis-network graph), so a node or metric reads identically across
# the whole report. Two families:
#   node KIND (who):  ground station / relay satellite / producer
#   METRIC (what):    tx / rx / cpu / memory / volume
KIND_GS, KIND_SAT, KIND_PROD = "#f58518", "#4c78a8", "#54a24b"   # estrack / relay / producer
MET = {"tx": "#72b7b2", "rx": "#e45756", "cpu": "#b279a2", "mem": "#eeca3b", "vol": "#9d755d"}
# File priority (UC3) — consistent across the priority comparison.
PRIO_COLORS = {"high": "#e45756", "default": "#f58518", "low": "#72b7b2"}
# Single dict handed to the JS templates so interactive charts use identical hex.
PALETTE = {"gs": KIND_GS, "sat": KIND_SAT, "prod": KIND_PROD, **MET}
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
    "duration": ("—", "Experiment duration on the simulation clock (simulationStartTime → experimentTime)"),
    "sim_start": ("UTC", "Simulation clock at experiment start (simulationStartTime)"),
    "sim_end": ("UTC", "Simulation clock when the experiment finished (experimentTime)"),
    "file_size": ("—", "Size of the produced photo file"),
    "priority": ("—", "File priority class (low/default/high; EDFS PAR)"),
    "t_destroy": ("—", "UC4: time from photo capture (≈t0) to the producer's Destroy event — the dt KPI axis"),
    "sat_count": ("sats", "Number of satellites in the constellation"),
    "RF": ("copies", "EDFS replication factor (n/a for TUS)"),
    "notes": ("—", "Data-quality flags for this run (missing metrics, undelivered files)"),
    "first_GS_delivery_s": ("s", "Time from sim start until the first produced file reached any ground station"),
    "last_GS_delivery_s": ("s", "Time from sim start until the last produced file reached a ground station (all-delivered time)"),
    "files_to_GS": ("count", "Distinct produced files that reached at least one ground station"),
    "delivery_success_rate": ("%", "Distinct files reaching ≥1 GS as a percentage of files produced (UC2/UC5 primary KPI)"),
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
              "files_to_GS", "delivery_success_rate", "all_delivered", "first_GS_delivery_s",
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
        kpis=["engine", "state", "file_size", "priority", "t_destroy", "sat_count", "RF",
              "delivered", "first_GS_delivery_s", "produced"] + _TAIL,
        cols=[("engine", "engine"), ("priority", "priority"), ("dt", "t_destroy"),
              ("sat", "sat_count"), ("RF", "rf"), ("delivered", "delivered"),
              ("firstGS(s)", "first_gs")]),
    "uc5": dict(headline="last_gs", hlabel="time-until-all-files-delivered (s)",
        kpis=["engine", "state", "sat_count", "RF", "produced", "files_to_GS",
              "delivery_success_rate", "all_delivered", "first_GS_delivery_s", "last_GS_delivery_s"] + _TAIL,
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
    td = re.search(r"-td(\d+[mh])", rid)   # UC4 time-to-destroy (the dt axis)
    return dict(engine=eng,
                file_size=fs.group(1).upper() if fs else "?",
                sat_count=int(sat.group(1)) if sat else 0,
                priority=pr.group(1) if pr else ("-" if eng == "tus" else "default"),
                rf=int(rf.group(1)) if rf else None,
                t_destroy=td.group(1) if td else None)


def load_yaml(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def human_size(n):
    """Bytes -> short human string matching the run_id tokens (e.g. 33554432 -> 32M)."""
    n = int(n)
    for unit, div in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if n >= div:
            v = n / div
            return f"{v:.0f}{unit}" if v == int(v) else f"{v:.1f}{unit}"
    return f"{n}B"


def crud_put_summary(bundle):
    """(file_size_str, set_of_distinct_put_names) from events-csv/crud.csv PUT rows.

    crud.csv carries an `attributes` column that is a quoted JSON object containing
    commas, so it MUST be read quote-aware (read_events uses csv.DictReader) — a naive
    split shifts every column after `attributes` and corrupts `name`/`size`. File size
    comes from the dedicated `size` column (NOT the JSON); the PUT-name set is the
    distinct values of the parsed `name` column."""
    puts = [r for r in read_events(os.path.join(bundle, "events-csv", "crud.csv"))
            if (r.get("type") or "").upper() == "PUT"]
    names = {r.get("name") for r in puts if r.get("name")}
    sizes = [int(r["size"]) for r in puts if (r.get("size") or "").strip().isdigit()]
    size_str = human_size(max(set(sizes), key=sizes.count)) if sizes else None
    return size_str, names


def compute(bundle, rid):
    info = parse_run_id(rid)
    m = lambda n: os.path.join(bundle, "metrics-csv", n + ".csv")
    # file size + produced-file count from crud.csv (quote-aware): the run_id only
    # carries a size token for UC1, and the produced count is a PUT-name count.
    crud_sizes_names = crud_put_summary(bundle)
    crud_size, put_names = crud_sizes_names
    if crud_size:
        info["file_size"] = crud_size

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
    # produced-file count = distinct file names PUT (crud.csv, quote-aware) UNION
    # distinct file names that reached a ground station (file_delivered.csv). The
    # union covers incomplete PUT logs whose delivered files are absent from crud.
    # Fall back to the yass_file_produced_total metric when neither source has rows.
    # events-csv fallback: earliest GS delivery per file from file_delivered.csv.
    # Used when the delivery histogram (metrics-csv) is empty — e.g. large-roster
    # runs that have no metrics-csv — so the headline first/last-GS KPIs are not "n/a".
    gs_event_secs = {}
    for r in read_events(os.path.join(bundle, "events-csv", "file_delivered.csv")):
        if not (r.get("target") or "").startswith("estrack"):
            continue
        nm = r.get("name") or r.get("target")
        ds = _to_float(r.get("deliverySeconds"))
        if nm and (nm not in gs_event_secs or ds < gs_event_secs[nm]):
            gs_event_secs[nm] = ds
    delivered_names = set(gs_event_secs)
    produced_set = (put_names or set()) | delivered_names
    produced = float(len(produced_set)) if produced_set else sum(v for _, v, _ in read_metric(m("yass_file_produced_total")))

    exp = (load_yaml(os.path.join(bundle, "manifests", "experiment.yaml"))
           or load_yaml(os.path.join(bundle, "experiment.yaml")) or {})
    status = exp.get("status") or {}
    state = status.get("experimentState") or "?"
    spec = exp.get("spec") or {}

    # Execution date = CR creation (wall clock). Duration = the *simulation* span
    # (simulationStartTime -> experimentTime): the CR condition lastTransitionTimes
    # only move during setup (they freeze ~30s in), so they badly under-report the
    # real run length and contradict the per-file delivery times. The sim clock is
    # the meaningful, self-consistent duration. Fall back to creation->last-recon.
    created = (exp.get("metadata") or {}).get("creationTimestamp")
    c0 = parse_ts(created)
    sim0 = parse_ts(spec.get("simulationStartTime"))
    sim1 = parse_ts(status.get("experimentTime"))
    if sim0 and sim1 and sim1 > sim0:
        duration_s = (sim1 - sim0).total_seconds()
    else:
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
    # Histogram-based when present; otherwise fall back to file_delivered.csv events
    # so first/last-GS and the delivered-file count survive an empty metrics-csv.
    first_gs = min(gs.values()) if gs else (min(gs_event_secs.values()) if gs_event_secs else None)
    last_gs = (max(producer_first.values()) if producer_first
               else (max(gs_event_secs.values()) if gs_event_secs else None))
    files_to_gs = len(producer_first) if producer_first else len(gs_event_secs)

    # delivery_success_rate (UC2/UC5 primary KPI): distinct delivered FILE names
    # (events-csv) over the DETERMINISTIC produced-file count for the UC — sat_count
    # for UC2, max(1, floor(0.2·sat))·5 for UC5 (per the UC READMEs). Using the
    # deterministic denominator (not the empirical crud count) keeps the rate bounded
    # and engine-comparable. UC1/UC3/UC4 do not headline this rate.
    _uc = rid.split("-")[0]
    _sat = info.get("sat_count") or 0
    if _uc == "uc5":
        succ_denom = max(1, int(0.2 * _sat)) * 5 if _sat else 0
    elif _uc == "uc2":
        succ_denom = _sat
    else:
        succ_denom = produced
    delivery_success_rate = (100.0 * len(delivered_names) / succ_denom) if succ_denom else None

    # notes — data-quality flags for the run (spec metadata card `notes`).
    notes = []
    if not cpu and not mem:
        notes.append("no CPU/memory metrics (metrics-csv absent)")
    if not gs and gs_event_secs:
        notes.append("delivery times from events-csv (no histogram)")
    if produced and files_to_gs < round(produced):
        notes.append(f"{int(round(produced)) - files_to_gs} of {int(round(produced))} file(s) undelivered")
    notes_str = "; ".join(notes) or "—"

    return dict(
        run_id=rid, **info,
        state=state,
        exec_date=created, duration_s=duration_s,
        sim_start=spec.get("simulationStartTime"),
        sim_end=status.get("experimentTime"),
        first_gs=first_gs,
        last_gs=last_gs,
        mean_gs=(sum(gs.values()) / len(gs)) if gs else None,
        n_gs=len(gs),
        gs_reached=gs_reached,
        files_to_gs=files_to_gs,
        delivery_success_rate=delivery_success_rate,
        producers=sorted({s for (s, _t) in per_t if s}),
        all_delivered=bool(produced) and files_to_gs >= round(produced),
        delivered=files_to_gs >= 1,
        n_receivers=len([r for r in recv if r]),
        per_target=per_t,
        gs_targets=gs,
        produced=produced,
        notes=notes_str,
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
        "priority": k["priority"], "t_destroy": k.get("t_destroy") or "n/a",
        "sat_count": k["sat_count"],
        "RF": k["rf"] if k["rf"] is not None else "n/a",
        "first_GS_delivery_s": fg, "last_GS_delivery_s": lg,
        "files_to_GS": k["files_to_gs"],
        "delivery_success_rate": f"{k['delivery_success_rate']:.0f}" if k.get("delivery_success_rate") is not None else "n/a",
        "all_delivered": "yes" if k["all_delivered"] else "no",
        "delivered": "yes" if k["delivered"] else "no",
        "GS_with_delivery": k["n_gs"],
        "GS_reached": k["gs_reached"], "distinct_receivers": k["n_receivers"],
        "produced": f"{k['produced']:.0f}", "notes": k.get("notes") or "—",
        "TX_MiB": f"{k['tx_mib']:.0f}",
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
    plt.barh(labels, vals, color=KIND_GS)  # ground stations — KIND_GS everywhere
    plt.xlabel("delivery time (s)")
    plt.title(f"{k['run_id']} — per-GS delivery")
    plt.tight_layout()
    plt.savefig(outpng, dpi=110)
    plt.close()
    return True


def chart_bar(pairs, ylabel, title, outpng, top=12, colors=None):
    # When per-bar colours are given they are zipped with the pairs and sorted
    # together, so a bar keeps its colour regardless of the value sort.
    if colors is not None:
        z = sorted(zip(pairs, colors), key=lambda x: x[0][1], reverse=True)[:top]
        pairs = [p for p, _ in z]; bar_colors = [c for _, c in z]
    else:
        pairs = sorted(pairs, key=lambda x: x[1], reverse=True)[:top]; bar_colors = MET["tx"]
    if not pairs:
        return False
    plt.figure(figsize=(9, 4))
    plt.bar([p[0] for p in pairs], [p[1] for p in pairs], color=bar_colors)
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
            f"plot 'delivery.dat' using 2:xtic(1) lc rgb '{KIND_GS}' notitle\n")


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


def kind_tag(name, producers=()):
    """Colour class of a node: 'gs' (ground station), 'prod' (producer) or 'sat'
    (relay satellite). Drives the single canonical PALETTE across every chart."""
    if name in producers:
        return "prod"
    return _node_kind(name)


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build_propagation(bundle, sim_start=None, duration_s=None):
    """Per-file transfer edges for the propagation graph.
    EDFS: events-csv/block_recv.csv (multi-source, per block, from the bitswap
    tracer). TUS / fallback: events-csv/file_delivered.csv (origin->target star).
    The timeline is anchored at the experiment's simulation start (sim_start) and
    runs to its end (duration_s), so both graphs span the WHOLE experiment, not
    just the window from the first transfer.
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
    # Anchor the timeline at the experiment start (simulationStartTime) so the slider
    # spans the whole run; fall back to the first transfer only when the manifest
    # lacks a start time. tmax = experiment duration (so the axis reaches the end).
    t0 = parse_ts(sim_start) or (min(ts) if ts else None)

    # block_recv labels the unixfs root block with the FILE NAME but every data
    # chunk with its own block CID, so grouping by the raw `file` field floods the
    # picker with hundreds of per-chunk CIDs and a single-CID view shows only one
    # block's hop (often a GS<->GS bitswap re-share, with no producer edge visible).
    # Re-key every block to a real file name so the graph groups per file: rows
    # already carrying a name keep it; chunk-CID rows are attributed to the sole
    # file, or to the file whose producer matches the sender.
    cid_re = re.compile(r'^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[A-Za-z2-7]{50,})$')
    named = {r.get("name") for r in delivered if r.get("name")}
    named |= {f for _, f, *_ in raw if f and not cid_re.match(f)}
    named.discard(None); named.discard("")
    one_file = next(iter(named)) if len(named) == 1 else None

    def file_of(f, frm):
        if f and not cid_re.match(f):
            return f                       # already a file name (root block)
        if one_file:
            return one_file                # single-file run: all chunks -> it
        for fn in named:                   # multi-file: attribute by producer prefix
            if fn.startswith((frm or "") + "_"):
                return fn
        return "(relayed chunks)"

    # Drop 0-byte rows: those are bitswap control messages (WANT/HAVE/DONT_HAVE),
    # not data transfers — they create phantom GS<->GS "exchange" edges before any
    # real bytes have moved. Bucket times to BUCKET_S to absorb cross-node sim-clock
    # skew: a producer send and its near-instant re-share land in the same LOS
    # window but the per-node telemetry timestamps can invert by ~1s, which would
    # otherwise show ground stations swapping the file before the producer sent it.
    BUCKET_S = 2.0
    events, node_ids, files = [], set(), set()
    for t, f, frm, to, b in raw:
        if b <= 0:
            continue
        fn = file_of(f, frm)
        rel = round((t - t0).total_seconds() / BUCKET_S) * BUCKET_S if (t and t0) else 0.0
        rel = max(0.0, rel)  # clamp pre-start clock skew to the experiment start
        events.append({"t": rel, "file": fn, "from": frm, "to": to, "bytes": b})
        node_ids.update((frm, to)); files.add(fn)

    nodes = [{"id": n, "kind": _node_kind(n), "producer": n in producers}
             for n in sorted(node_ids)]

    # acq[file][node] = time the node first held the file. A node that sends a file
    # but never receives it is its origin/producer (acq = 0). Used by the causality
    # filter (a node can't transfer a file before it has it) and the spread tree.
    acq, tot = {}, {}
    for e in events:
        a = acq.setdefault(e["file"], {})
        if e["to"] not in a or e["t"] < a[e["to"]]:
            a[e["to"]] = e["t"]
        k = (e["file"], e["from"], e["to"])
        tot[k] = tot.get(k, 0) + e["bytes"]
    for f in files:
        a = acq.setdefault(f, {})
        for origin in {e["from"] for e in events if e["file"] == f} - set(a):
            a[origin] = 0.0

    # Propagation tree: ONE incoming edge per node, grown by BFS outward from the
    # origins over REAL transfer edges only. A node is attached the moment one of its
    # actual senders is already reachable from an origin; among reachable senders we
    # prefer the latest holder that had the file before this node did (most direct
    # hop), else the earliest reachable holder. Because every parent is a genuine
    # sender, every tree edge carries real bytes — no phantom 0-byte connector
    # bridging two otherwise-separate clusters. Nodes with no real path back to an
    # origin are simply omitted rather than joined by a fake edge (honest: we can't
    # show a transfer that did not happen). The full mesh stays in `events`.
    senders_of = {}  # (file,to) -> {from: acq_from}  (real edges, bytes>0)
    for e in events:
        if e["from"] == e["to"]:
            continue
        senders_of.setdefault((e["file"], e["to"]), {})[e["from"]] = acq[e["file"]].get(e["from"], float("inf"))
    tree = []
    for f in files:
        reached = {n for n, a in acq[f].items() if a == 0.0}     # origins (producers)
        pending = {to for (ff, to) in senders_of if ff == f} - reached
        progress = True
        while progress and pending:
            progress = False
            for to in sorted(pending, key=lambda n: acq[f].get(n, 0.0)):
                cand = {frm: af for frm, af in senders_of[(f, to)].items() if frm in reached}
                if not cand:
                    continue
                ato = acq[f].get(to, 0.0)
                causal = {frm: af for frm, af in cand.items() if af <= ato}
                frm = max(causal, key=causal.get) if causal else min(cand, key=cand.get)
                tree.append({"t": ato, "file": f, "from": frm, "to": to,
                             "bytes": tot.get((f, frm, to), 0)})
                reached.add(to); pending.discard(to); progress = True

    tmax = max((duration_s or 0.0), max((e["t"] for e in events), default=0.0))
    return {"source": used, "files": sorted(files), "nodes": nodes,
            "events": events, "tree": tree, "acq": acq, "tmax": tmax}


def build_deliveries(bundle):
    """Per-file propagation timeline from events-csv/file_delivered.csv. Sorted by
    file, then by time. The first row of each file is the PRODUCER at t0 (it holds
    the file from creation, so #fsNodes-with-file = 1); each subsequent row is the
    next fsNode to receive the full file, with #fsNodes-with-file incremented.
    Works for both engines."""
    # sim_time column = elapsed since the experiment (simulation) started, not a date
    exp = (load_yaml(os.path.join(bundle, "manifests", "experiment.yaml"))
           or load_yaml(os.path.join(bundle, "experiment.yaml")) or {})
    sim0 = parse_ts((exp.get("spec") or {}).get("simulationStartTime"))

    def simfmt(dt):
        if not dt:
            return ""
        return fmt_dur((dt - sim0).total_seconds()) + " from start" if sim0 else dt.strftime("%m-%d %H:%M:%S")
    per_file = {}  # name -> {"producers": {src:n}, "rx": {receiver:(sec,et)}, "create": dt}
    for r in read_events(os.path.join(bundle, "events-csv", "file_delivered.csv")):
        name, tgt, src = r.get("name"), r.get("target"), r.get("source")
        if not name or not tgt:
            continue
        d = per_file.setdefault(name, {"producers": {}, "rx": {}, "create": None})
        if src:
            d["producers"][src] = d["producers"].get(src, 0) + 1
        sec = _to_float(r.get("deliverySeconds"))
        et = parse_ts(r.get("experimentTime"))
        if d["create"] is None and et is not None:
            d["create"] = et - timedelta(seconds=sec)   # file creation = delivery - age
        if tgt not in d["rx"] or sec < d["rx"][tgt][0]:  # earliest if repeated
            d["rx"][tgt] = (sec, et)
    rows = []
    for name in sorted(per_file):
        d = per_file[name]
        producer = max(d["producers"], key=d["producers"].get) if d["producers"] else None
        holders = 0
        if producer:
            holders = 1
            rows.append({"file": name, "fsNode": producer, "delivery_s": 0.0,
                         "holders": 1, "is_gs": _node_kind(producer) == "gs",
                         "is_producer": True, "sim_time": simfmt(d["create"])})
        for tgt, (sec, et) in sorted(d["rx"].items(), key=lambda x: x[1][0]):
            if tgt == producer:
                continue
            holders += 1
            rows.append({"file": name, "fsNode": tgt, "delivery_s": round(sec, 1),
                         "holders": holders, "is_gs": _node_kind(tgt) == "gs",
                         "is_producer": False, "sim_time": simfmt(et)})
    return rows

def metric_series(bundle, metric, scale=1.0):
    """Per-timestamp sum across all series of a time-series metric.
    Returns (rel_seconds[], total[]) with total divided by `scale`. The metric CSV
    has label columns followed by ISO-timestamp value columns."""
    p = os.path.join(bundle, "metrics-csv", metric + ".csv")
    if not os.path.exists(p):
        return [], []
    with open(p) as f:
        rd = csv.reader(f)
        header = next(rd, [])
        rows = list(rd)
    ts_idx = [i for i, h in enumerate(header) if h.startswith("20")]
    if not ts_idx:
        return [], []
    ts = [parse_ts(header[i]) for i in ts_idx]
    t0 = next((t for t in ts if t), None)
    rel = [round((t - t0).total_seconds()) if (t and t0) else 0 for t in ts]
    totals = []
    for j in ts_idx:
        s = 0.0
        for row in rows:
            if j < len(row):
                try:
                    s += float(row[j])
                except (ValueError, TypeError):
                    pass
        totals.append(round(s / scale, 2))
    return rel, totals


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


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


ENG_COLORS = {"edfs": "#4c78a8", "tus": "#e45756"}


def engine_compare(uc_id, rows, ucdir, cfg):
    """EDFS-vs-TUS comparison PNGs into ucdir/charts. Only for UCs that ran both
    engines. Returns [{src, caption}]."""
    if not ({"edfs", "tus"} <= {r["engine"] for r in rows}):
        return []
    cdir = os.path.join(ucdir, "charts"); os.makedirs(cdir, exist_ok=True)
    metric, label = cfg["headline"], cfg["hlabel"]
    out = []

    # 1) headline delivery metric vs constellation size — grouped bars EDFS|TUS.
    sats = sorted({r["sat_count"] for r in rows if r.get(metric) is not None})
    if sats:
        x = list(range(len(sats)))
        w = 0.38
        plt.figure(figsize=(9, 5))
        for eng, off in (("edfs", -w / 2), ("tus", w / 2)):
            vals = [_mean([r.get(metric) for r in rows
                           if r["engine"] == eng and r["sat_count"] == s]) for s in sats]
            plt.bar([i + off for i in x], [v or 0 for v in vals], w,
                    label=eng.upper(), color=ENG_COLORS[eng])
        plt.xticks(x, [str(s) for s in sats]); plt.xlabel("sat_count"); plt.ylabel(label)
        plt.title(f"{uc_id.upper()} — {label}: EDFS vs TUS")
        plt.legend(); plt.grid(True, axis="y", alpha=.3); plt.tight_layout()
        plt.savefig(os.path.join(cdir, "compare_delivery.png"), dpi=110); plt.close()
        out.append({"src": "charts/compare_delivery.png",
                    "caption": f"{label}: EDFS vs TUS (mean per sat_count)"})

    # 2) secondary metrics (resource cost) — 2x2 grouped bars, mean across runs.
    metrics = [("peak_mem_mib", "peak RAM (MiB)"), ("peak_cpu", "peak CPU (millicores)"),
               ("tx_mib", "network TX (MiB)"), ("rx_mib", "network RX (MiB)")]
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    for ax, (key, lbl) in zip(axes.flat, metrics):
        e = _mean([r[key] for r in rows if r["engine"] == "edfs"]) or 0
        t = _mean([r[key] for r in rows if r["engine"] == "tus"]) or 0
        ax.bar(["EDFS", "TUS"], [e, t], color=[ENG_COLORS["edfs"], ENG_COLORS["tus"]])
        ax.set_title(lbl); ax.grid(True, axis="y", alpha=.3)
    fig.suptitle(f"{uc_id.upper()} — resource usage: EDFS vs TUS (mean across runs)")
    fig.tight_layout()
    fig.savefig(os.path.join(cdir, "compare_resources.png"), dpi=110); plt.close(fig)
    out.append({"src": "charts/compare_resources.png",
                "caption": "Resource usage (RAM, CPU, TX, RX): EDFS vs TUS (mean across runs)"})
    return out

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

    producers = set(k.get("producers") or [])
    chart_delivery(k, os.path.join(cdir, "delivery.png"))
    # Network TX by node (top 20), kind-coloured to match the interactive U3 chart
    # (same aggregation — aggregate_net — and same palette).
    _txn = aggregate_net(bundle, "yass_network_tx_bytes_total")
    _txtop = sorted(_txn, key=lambda n: _txn[n], reverse=True)[:20]
    chart_bar([(n, _txn[n] / MiB) for n in _txtop], "TX MiB",
              f"{k['run_id']} — network TX by node", os.path.join(cdir, "network_tx.png"),
              top=20, colors=[PALETTE[kind_tag(n, producers)] for n in _txtop])
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
        t = tgt or "?"
        deliv[t] = {"s": round(sec, 1), "gs": bool(t.startswith("estrack")),
                    "kind": kind_tag(t, producers)}
    tx = aggregate_net(bundle, "yass_network_tx_bytes_total")
    rx = aggregate_net(bundle, "yass_network_rx_bytes_total")
    # U3 — TX per node (top 20), kind-tagged so bars are coloured estrack/sat/producer.
    nodes = sorted(set(tx) | set(rx), key=lambda n: tx.get(n, 0), reverse=True)[:20]
    data = {"delivery": deliv, "producers": sorted(producers), "pal": PALETTE,
            "net": {"nodes": nodes,
                    "tx": [round(tx.get(n, 0) / MiB, 1) for n in nodes],
                    "rx": [round(rx.get(n, 0) / MiB, 1) for n in nodes],
                    "kind": [kind_tag(n, producers) for n in nodes]}}
    # U1 — cumulative network TX/RX (MiB) over time
    ntx_t, ntx = metric_series(bundle, "yass_network_tx_bytes_total", MiB)
    nrx_t, nrx = metric_series(bundle, "yass_network_rx_bytes_total", MiB)
    data["net_ts"] = {"t": ntx_t or nrx_t, "tx": ntx, "rx": nrx}
    # U2 — aggregate CPU (millicores) + memory (MiB) over time
    cpu_t, cpu_v = metric_series(bundle, "yass_container_cpu_millicores")
    mem_t, mem_v = metric_series(bundle, "yass_container_memory_bytes", MiB)
    data["res_ts"] = {"t": cpu_t or mem_t, "cpu": cpu_v, "mem": mem_v}
    # U4 / U5 — peak CPU + memory per fsNode (top 20), kind-tagged. Per the spec,
    # the node value is each container's peak (max over time) SUMMED across that
    # fsNode's containers — so a multi-container EDFS node shows its whole footprint,
    # not just its single heaviest container.
    def _peak_by_node(metric, scale):
        d = {}
        for l, _f, peak in read_metric(os.path.join(bundle, "metrics-csv", metric + ".csv")):
            nd = l.get("fsNode") or l.get("peer_node") or "?"
            d[nd] = d.get(nd, 0.0) + peak / scale
        top = sorted(d, key=lambda n: d[n], reverse=True)[:20]
        return {"nodes": top, "vals": [round(d[n], 1) for n in top],
                "kind": [kind_tag(n, producers) for n in top]}
    data["cpu_by_node"] = _peak_by_node("yass_container_cpu_millicores", 1.0)
    data["mem_by_node"] = _peak_by_node("yass_container_memory_bytes", MiB)
    # volume used — global over time + peak per node (disk; UC1/UC5 relevant)
    vol_t, vol = metric_series(bundle, "yass_volume_used_bytes", MiB)
    data["vol_ts"] = {"t": vol_t, "used": vol}
    voln = {}
    for l, _f, peak in read_metric(os.path.join(bundle, "metrics-csv", "yass_volume_used_bytes.csv")):
        nd = l.get("fsNode") or l.get("peer_node") or "?"
        voln[nd] = max(voln.get(nd, 0), peak)
    vnodes = sorted(voln, key=lambda n: voln[n], reverse=True)[:20]
    data["vol_by_node"] = {"nodes": vnodes, "used": [round(voln[n] / MiB, 1) for n in vnodes],
                           "kind": [kind_tag(n, producers) for n in vnodes]}

    graph = build_propagation(bundle, k.get("sim_start"), k.get("duration_s"))
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
    if "notes" not in kpi_keys:        # data-quality flags (spec metadata card)
        kpi_keys.append("notes")

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
    # EDFS-vs-TUS comparison PNGs into ucN/charts/ — generated but NOT linked on
    # the UC index page (kept as hidden deliverable artifacts).
    engine_compare(uc_id, rows, ucout, cfg)

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
