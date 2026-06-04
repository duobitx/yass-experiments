#!/usr/bin/env python3
"""Regenerate the per-`sat_count` Layouts for a UC experiment.

Why: Layouts are static YAML on disk so they're trivially diff-able,
but their content is derived from a frozen OneWeb TLE roster + the
spain-shot ESTRACK GS coordinates. The roster is owned by the UCs
(`_common_/oneweb-roster.yaml`) and is deliberately independent of the
big-scale experiment, so the two can evolve separately. If either
source changes, every UC's sweep layouts must be regenerated — this
script keeps the derivation reproducible and shared across UCs.

Inputs (paths are relative to this script's location):
  ./oneweb-roster.yaml                  satellite TLEs (OneWeb)
  ../spain-shot/base/01_layout.yaml     seven ESTRACK GS blocks

Outputs (overwritten in --target-dir): one nNN.yaml per --counts entry.

Selection algorithm — plane-diverse round-robin:
  1. Parse each satellite's RAAN from line 2 of its TLE.
  2. Bucket sats by floor(RAAN / 20) * 20 — ~20° plane window.
  3. Sort each bucket deterministically by sat name.
  4. Round-robin across buckets in sorted-key order, picking one sat
     from each bucket per pass.
  5. The first plane-diverse pick is the producer for every UC that
     uses a single producer (UC1, UC3, UC4).

Counts larger than the roster (currently 60 real OneWeb sats) are
topped up with synthetic OneWeb-like sats (NORAD 90000+, names
oneweb-9NNN sorting after every real sat) spread evenly across orbital
planes. Every real sat — and therefore every UC's hardcoded producer —
is always included before any synthetic sat is added.

Run:
  python3 regenerate-uc-layouts.py --target-dir ../uc1-rapid-disaster-response/_layouts --name-prefix uc1
"""
import argparse
import re
import pathlib
import sys
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ONEWEB_ROSTER = HERE / "oneweb-roster.yaml"
SPAIN_SHOT = HERE.parent / "spain-shot" / "base" / "01_layout.yaml"

# Sweep granularity used by every UC. If a UC needs a different set
# (e.g. UC4 with only n01 / n08 / n55) the caller passes --counts.
DEFAULT_COUNTS = [1, 2, 8, 21, 55]

def parse_sat_blocks(layout_yaml: str):
    """Return list of (name, RAAN_deg, raw_block_text) for each OneWeb sat."""
    block_re = re.compile(r"(  - fsNode: oneweb-[\w-]+\n(?:    .*\n)+)", re.MULTILINE)
    blocks = block_re.findall(layout_yaml)
    out = []
    for b in blocks:
        name = re.search(r"fsNode: (oneweb-\w+)", b).group(1)
        m = re.search(r'"2 \d+\s+\d+\.\d+\s+(\d+\.\d+)\s+', b)
        raan = float(m.group(1)) if m else -1.0
        out.append((name, raan, b))
    return out

def parse_gs_blocks(layout_yaml: str):
    return re.compile(r"(  - fsNode: estrack-[\w-]+\n(?:    .*\n)+)", re.MULTILINE).findall(layout_yaml)

# Synthetic OneWeb-like template, derived from real OneWeb element values
# (incl 87.91°, e≈0.0002, mean motion 13.166 rev/day ≈ 1200 km). Only the
# satellite number, RAAN and mean anomaly vary; everything else is fixed.
SYNTH_EPOCH   = "26145.50000000"
SYNTH_DRAG    = " .00000050  00000+0  10000-4 0"
SYNTH_INCL    = "87.9106"
SYNTH_ECC     = "0002299"
SYNTH_ARGP    = "92.4563"
SYNTH_MEANMOT = "13.16593850"
SYNTH_PLANES  = 18

def _tle_checksum(line: str) -> int:
    return sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:-1]) % 10

def _with_checksum(line: str) -> str:
    return line[:-1] + str(_tle_checksum(line))

def gen_synthetic(count):
    """Return `count` (name, RAAN, block) synthetic sats, plane-diverse:
    consecutive indices step to the next orbital plane so a simple prefix
    slice stays spread across planes."""
    out = []
    for i in range(count):
        plane = i % SYNTH_PLANES
        slot = i // SYNTH_PLANES
        raan = (plane * 360.0 / SYNTH_PLANES) % 360.0
        ma = (slot * 360.0 / SYNTH_PLANES + plane * 11.0) % 360.0
        norad = 90000 + i
        name = f"oneweb-9{i:03d}"
        intl = f"26001{chr(ord('A') + i % 26)}  "
        l1 = _with_checksum(
            f"1 {norad:05d}U {intl} {SYNTH_EPOCH} {SYNTH_DRAG}  9990")
        l2 = _with_checksum(
            f"2 {norad:05d}  {SYNTH_INCL} {raan:8.4f} {SYNTH_ECC}  "
            f"{SYNTH_ARGP} {ma:8.4f} {SYNTH_MEANMOT}000010")
        block = (
            f"  - fsNode: {name}\n"
            f"    nodeType: satellite\n"
            f"    orbit:\n"
            f"      tle:\n"
            f'        - "{l1}"\n'
            f'        - "{l2}"\n'
            f"    hardwareSpecRef: oneweb\n"
        )
        out.append((name, raan, block))
    return out

def round_robin(annotated):
    buckets = defaultdict(list)
    for name, raan, blk in annotated:
        buckets[int(raan // 20) * 20].append((name, raan, blk))
    for k in buckets:
        buckets[k].sort(key=lambda x: x[0])
    keys = sorted(buckets)
    idx = {k: 0 for k in keys}
    out = []
    while any(idx[k] < len(buckets[k]) for k in keys):
        for k in keys:
            if idx[k] < len(buckets[k]):
                out.append(buckets[k][idx[k]])
                idx[k] += 1
    return out

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-dir", required=True,
                   help="path to the UC's _layouts/ directory")
    p.add_argument("--name-prefix", required=True,
                   help="Layout metadata.name prefix, e.g. uc1 -> uc1-n01")
    p.add_argument("--counts", default=",".join(str(c) for c in DEFAULT_COUNTS),
                   help=f"comma-separated sat counts (default: {DEFAULT_COUNTS})")
    args = p.parse_args()

    target = pathlib.Path(args.target_dir).resolve()
    if not target.is_dir():
        print(f"target dir not found: {target}", file=sys.stderr)
        sys.exit(2)
    counts = [int(c) for c in args.counts.split(",") if c.strip()]

    annotated = parse_sat_blocks(ONEWEB_ROSTER.read_text())
    gs_blocks = parse_gs_blocks(SPAIN_SHOT.read_text())
    ordered = round_robin(annotated)
    extra = gen_synthetic(max(0, max(counts) - len(ordered)))
    print(f"producer = {ordered[0][0]}  (RAAN ≈ {ordered[0][1]:.1f}°); "
          f"{len(ordered)} real + {len(extra)} synthetic available")
    for n in counts:
        out_file = target / f"n{n:02d}.yaml"
        if n <= len(ordered):
            chosen = ordered[:n]
            synth = 0
        else:
            chosen = ordered + extra[:n - len(ordered)]
            synth = n - len(ordered)
        sel = ("round-robin across RAAN buckets for orbital-plane diversity"
               if synth == 0 else
               f"all {len(ordered)} real sats + {synth} synthetic OneWeb-like "
               "sats (oneweb-9NNN) spread across planes")
        header = (
            "apiVersion: int.esa.yass/v1\n"
            "kind: Layout\n"
            "metadata:\n"
            f"  name: {args.name_prefix}-n{n:02d}\n"
            "  annotations:\n"
            '    yass.experiments/source-tles: "../../_common_/oneweb-roster.yaml"\n'
            f'    yass.experiments/sat-selection: "{sel}"\n'
            "spec:\n"
        )
        out_file.write_text(header + "".join(b for _, _, b in chosen) + "".join(gs_blocks))
        raan_set = sorted({int(r // 20) * 20 for _, r, _ in chosen})
        print(f"wrote {out_file.relative_to(target.parent)}: {n} SAT "
              f"({synth} synthetic, RAAN buckets {raan_set}) + {len(gs_blocks)} GS")

if __name__ == "__main__":
    main()
