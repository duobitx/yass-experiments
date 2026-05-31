#!/usr/bin/env python3
"""
Build tools/tle-snapshot.txt by fetching several constellation groups from
celestrak and picking a diverse subset. The point of mixing constellations
is orbital diversity: the selection spans inclinations ~43..97 deg and a
range of altitudes, unlike a single constellation where every satellite
shares a near-identical orbit.

Per group the entries are stride-sampled so the chosen satellites are spread
across the group's orbital planes; obvious debris / rocket bodies are skipped
and names are sanitised to DNS-1123 labels usable as fsNode names.

Run this, then gen.py, to refresh the experiment:
    python3 tools/build-snapshot.py
    python3 tools/gen.py
"""

import pathlib
import re
import sys
import urllib.request

# group -> how many satellites to take from it. Inclinations (approx):
# Orbcomm 47, Globalstar 52, Starlink 53/70/97, Iridium-NEXT 86, OneWeb 88,
# Planet 53/97 — together a broad inclination/altitude spread.
GROUPS = [
    ("orbcomm", 6),
    ("globalstar", 8),
    ("starlink", 12),
    ("iridium-NEXT", 8),
    ("oneweb", 8),
    ("planet", 8),
]

URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={}&FORMAT=tle"
DEBRIS = re.compile(r"\b(deb|r/b|tba)\b", re.I)
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "tle-snapshot.txt"


def sanitise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch(group: str) -> list[tuple[str, str, str]]:
    with urllib.request.urlopen(URL.format(group), timeout=30) as resp:
        text = resp.read().decode()
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    recs = []
    i = 0
    while i + 2 <= len(lines) - 1:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            recs.append((name, l1, l2))
            i += 3
        else:
            i += 1
    return recs


def main():
    selected = []
    seen_name = set()
    seen_norad = set()
    for group, count in GROUPS:
        recs = [r for r in fetch(group) if not DEBRIS.search(r[0])]
        if not recs:
            sys.exit(f"group {group}: no usable TLEs fetched")
        picked = 0
        for i in range(count):
            k = (i * len(recs)) // count
            while k < len(recs):
                name, l1, l2 = recs[k]
                sn, norad = sanitise(name), l1[2:7]
                if sn and sn not in seen_name and norad not in seen_norad \
                        and len(l1) >= 69 and len(l2) >= 69:
                    seen_name.add(sn)
                    seen_norad.add(norad)
                    selected.append((sn, l1, l2))
                    picked += 1
                    break
                k += 1
        print(f"{group}: {picked}/{count}")

    OUT.write_text("".join(f"{sn}\n{l1}\n{l2}\n" for sn, l1, l2 in selected))
    print(f"wrote {OUT} ({len(selected)} satellites)")


if __name__ == "__main__":
    main()
