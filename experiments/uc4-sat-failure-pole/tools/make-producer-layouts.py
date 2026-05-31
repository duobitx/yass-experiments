#!/usr/bin/env python3
"""Build UC4's per-sat_count Layouts with a synthetic polar producer.

UC4 needs the producer to be over a pole and OUT OF LOS with every ESTRACK
ground station at t=0, then destroyed before its first GS contact. Instead of
hunting for a moment when a real OneWeb satellite happens to satisfy that, we
inject a purpose-built satellite `producer` whose orbit is designed for it.

Orbit of `producer`:
  - polar (inclination 90 deg) circular orbit, ~1200 km (mean motion 13.16),
  - mean anomaly 270 deg so the sub-satellite point is exactly over the SOUTH
    pole at the TLE epoch,
  - TLE epoch == the experiment simulationStartTime (2026-05-16T23:59:00Z), so
    propagation delta is zero and the sat is over the south pole at t=0.

The south pole is chosen deliberately: the southernmost ESTRACK station is
Malargüe (-35.8 deg), 54 deg of central angle from the pole — well beyond the
~28 deg line-of-sight horizon at this altitude. (The north pole would be
visible from Kiruna at 68 deg N.) So every ESTRACK station is out of LOS at
t=0, making the UC4 precondition true by construction.

This script runs the shared UC layout generator, then replaces the first
satellite (the round-robin producer slot) in each Layout with `producer`. The
remaining OneWeb satellites stay as relays.

Run:
    python3 tools/make-producer-layouts.py
"""

import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
UC4 = HERE.parent
COMMON = UC4.parent / "_common_"
LAYOUTS = UC4 / "_layouts"

PRODUCER_BLOCK = (
    "  - fsNode: producer\n"
    "    nodeType: satellite\n"
    "    orbit:\n"
    "      tle:\n"
    '        - "1 99999U 26001A   26136.99930556  .00000000  00000-0  00000-0 0  9998"\n'
    '        - "2 99999  90.0000   0.0000 0000001   0.0000 270.0000 13.16000000   108"\n'
    "    hardwareSpecRef: oneweb\n"
)

# First satellite block in a generated Layout (the producer slot).
FIRST_SAT_RE = re.compile(
    r"  - fsNode: oneweb-[\w-]+\n(?:    .*\n)+?(?=  - fsNode:)"
)


def main():
    subprocess.run(
        [sys.executable, str(COMMON / "regenerate-uc-layouts.py"),
         "--target-dir", str(LAYOUTS), "--name-prefix", "uc4"],
        check=True,
    )
    for f in sorted(LAYOUTS.glob("n*.yaml")):
        text = f.read_text()
        new, n = FIRST_SAT_RE.subn(PRODUCER_BLOCK, text, count=1)
        if n != 1:
            sys.exit(f"{f.name}: expected exactly one producer slot, replaced {n}")
        f.write_text(new)
        sats = new.count("nodeType: satellite")
        print(f"{f.name}: producer + {sats - 1} relay sat(s)")


if __name__ == "__main__":
    main()
