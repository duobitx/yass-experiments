#!/usr/bin/env python3
"""
Emit Layout + ExperimentDefinition + Namespace + kustomization.yaml for
every (n, engine) variant in the scaling experiment (PLAN.md §E1).

Variants: n ∈ {1, 2, 3, 5, 8}, engine ∈ {tus, edfs}.

The generator reads `relay-tles.yaml` (produced by gen-relay-tles.py) and
writes the per-n files in-place under ../nXX/. Idempotent: re-running
overwrites the same files.
"""

from __future__ import annotations

import os
import re
import sys

PRODUCER = "oneweb-0008"
PRODUCER_TLE = (
    "1 44059U 19010C   25347.49126494  .00000026  00000+0  35053-4 0  9990",
    "2 44059  87.9045 265.7535 0001501  76.4950 283.6348 13.16596955327295",
)
# Full ESTRACK set, copied verbatim from forever/base/01_layout.yaml.
GROUND_STATIONS = [
    ("estrack-new-norcia",   -31.048,  116.192),
    ("estrack-kiruna",        67.857,   20.964),
    ("estrack-redu",          50.000,    5.167),
    ("estrack-cebreros",      40.453,   -4.368),
    ("estrack-santa-maria",   36.97472, -25.09472),
    ("estrack-kourou",         5.251,  -52.805),
    ("estrack-malargue",     -35.776,  -69.398),
]
N_VALUES = [1, 2, 3, 5, 8]


def parse_relays(path: str) -> list[tuple[str, tuple[str, str]]]:
    """Lightweight parser: returns [(name, (l1, l2)), ...]."""
    relays = []
    name = None
    tles: list[str] = []
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            m = re.match(r"\s*-\s*name:\s*(\S+)\s*$", line)
            if m:
                if name is not None:
                    relays.append((name, (tles[0], tles[1])))
                name = m.group(1)
                tles = []
                continue
            m = re.match(r'\s*-\s*"(.*)"\s*$', line)
            if m:
                tles.append(m.group(1))
    if name is not None:
        relays.append((name, (tles[0], tles[1])))
    return relays


def layout(n: int, relays: list[tuple[str, tuple[str, str]]]) -> str:
    out = [
        "apiVersion: int.esa.yass/v1",
        "kind: Layout",
        "metadata:",
        "  name: scaling-layout",
        "spec:",
        f"  - fsNode: {PRODUCER}",
        "    nodeType: satellite",
        "    orbit:",
        "      tle:",
        f'        - "{PRODUCER_TLE[0]}"',
        f'        - "{PRODUCER_TLE[1]}"',
        "    rotation:",
        "      yaw: 0",
        "      roll: 0.5",
        "      pitch: 0",
        "    hardwareSpecRef: sentinel-2",
    ]
    for i in range(n - 1):
        name, (l1, l2) = relays[i]
        out += [
            f"  - fsNode: {name}",
            "    nodeType: satellite",
            "    orbit:",
            "      tle:",
            f'        - "{l1}"',
            f'        - "{l2}"',
            "    rotation:",
            "      roll: 0.5",
            "    hardwareSpecRef: sentinel-2",
        ]
    for gs_name, lat, lng in GROUND_STATIONS:
        out += [
            f"  - fsNode: {gs_name}",
            "    nodeType: groundStation",
            "    earthPosition:",
            f"      lat: {lat}",
            f"      lng: {lng}",
            "      heightOverSeaLevel: 0",
            "    hardwareSpecRef: ground-station-hwdef",
        ]
    return "\n".join(out) + "\n"


def expdef(n: int, relays: list[tuple[str, tuple[str, str]]]) -> str:
    out = [
        "apiVersion: int.esa.yass/v1",
        "kind: ExperimentDefinition",
        "metadata:",
        "  name: scaling",
        "spec:",
        '  maxDuration: "168h"',
        "  behaviours:",
        f"    - fsNode: {PRODUCER}",
        "      agent:",
        "        image: ghcr.io/duobitx/yass-agent-periodic",
        "        envsMap:",
        '          FILE_SIZE: "10M"',
        '          CHECK_INTERVAL_SECONDS: "120"',
        "      hardwareEvents: []",
    ]
    for i in range(n - 1):
        name, _ = relays[i]
        out += [
            f"    - fsNode: {name}",
            "      agent:",
            "        image: ghcr.io/duobitx/yass-agent-receive-only",
            "      hardwareEvents: []",
        ]
    for gs_name, _, _ in GROUND_STATIONS:
        out += [
            f"    - fsNode: {gs_name}",
            "      agent:",
            "        image: ghcr.io/duobitx/yass-agent-receive-only",
            "      hardwareEvents: []",
        ]
    return "\n".join(out) + "\n"


def namespace(name: str) -> str:
    return (
        "apiVersion: v1\n"
        "kind: Namespace\n"
        "metadata:\n"
        f"  name: {name}\n"
        "  labels:\n"
        '    yass-namespace: "true"\n'
    )


def kustomization_base() -> str:
    return (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        "  - 01_layout.yaml\n"
        "  - 02_experiment_def.yaml\n"
    )


def kustomization(engine: str, ns: str) -> str:
    return (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        "  - 00_namespace.yaml\n"
        "  - ../base\n"
        f"  - ../../_engines/{engine}\n"
        f"namespace: {ns}\n"
    )


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    print(f"wrote {os.path.relpath(path, root)}")


here = os.path.dirname(os.path.abspath(__file__))
root = os.path.normpath(os.path.join(here, ".."))


def main():
    relays = parse_relays(os.path.join(root, "relay-tles.yaml"))
    if len(relays) < max(N_VALUES) - 1:
        sys.exit(f"need {max(N_VALUES) - 1} relays, got {len(relays)}")

    for n in N_VALUES:
        n_dir = os.path.join(root, f"n{n:02d}")
        base_dir = os.path.join(n_dir, "base")
        write(os.path.join(base_dir, "01_layout.yaml"), layout(n, relays))
        write(os.path.join(base_dir, "02_experiment_def.yaml"), expdef(n, relays))
        write(os.path.join(base_dir, "kustomization.yaml"), kustomization_base())
        for engine in ("tus", "edfs"):
            ns = f"scaling-n{n:02d}-{engine}"
            v_dir = os.path.join(n_dir, engine)
            write(os.path.join(v_dir, "00_namespace.yaml"), namespace(ns))
            write(os.path.join(v_dir, "kustomization.yaml"), kustomization(engine, ns))


if __name__ == "__main__":
    main()
