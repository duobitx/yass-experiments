# Experiments

Each subdirectory is a self-contained experiment that can be applied with
`kubectl apply -k <dir>`.

## Use cases


- [`uc1-rapid-disaster-response/`](./uc1-rapid-disaster-response/) —
  Single producer; **time-to-first-delivery** as a function of file size
  `S`, priority `P`, constellation size and (EDFS-only) replication
  factor `RF`. EDFS vs TUS.
- [`uc2-continuous-los-relay/`](./uc2-continuous-los-relay/) — Every sat
  produces one file under a **continuous random fault stream** (no
  `Destroy`). Headline KPI: delivery success rate ≥ 95%. EDFS vs TUS.
- [`uc3-priority-aware-routing/`](./uc3-priority-aware-routing/) — Same
  scenario at `priority ∈ {low, default, high}` on a deterministic
  orbital schedule. Does EDFS actually privilege high-priority files?
  EDFS only.
- [`uc4-sat-failure-pole/`](./uc4-sat-failure-pole/) — Producer takes a
  polar shot and is killed by a `Destroy` event **before** its first LOS
  with any GS. Does EDFS still deliver via ISL? EDFS vs TUS (TUS is a
  negative-control baseline).
- [`uc5-general-failure/`](./uc5-general-failure/) — 20% of sats each
  produce 5 files under a continuous non-`Destroy` fault stream.
  Long-running stress test for eventual consistency. EDFS only.

## Supporting / older experiments

- [`spain-shot/`](./spain-shot/) — single-orbit scenario over Madrid:
  one ONEWEB satellite takes a 2 G photo, seven ESTRACK ground stations
  receive. Has `base/`, `tus/`, `edfs/` overlays plus a `hwfaults/`
  variant that exercises every hardware-fault type in seconds rather
  than minutes (used as the canonical smoke-test for the
  hardware-events injector).
- [`forever/`](./forever/) — long-running constellation (3 satellites +
  7 ESTRACK ground stations) used to exercise sustained data flow over
  the simulated network. Has `edfs/` and `tus/` variants on top of a
  shared `base/` layout.
- [`scaling/`](./scaling/) — delivery time to a dedicated ground station
  as a function of constellation size (`n ∈ {1, 2, 3, 5, 8}`), TUS vs
  EDFS. Per-n overlays + sequential runner.
- [`big-scale/`](./big-scale/) — 60-satellite constellation, used to
  exercise the simulator and observability at non-trivial cardinality.
- [`networking-demo/`](./networking-demo/) — minimal connectivity demo
  (3 satellites + 1 ground station, `fs_engine_udp_ping` engine).
  Verifies that `world-controller` actually shapes traffic with tc.
- [`_common_/`](./_common_/) — shared `HardwareDefinition` set; not an
  experiment, included as a base.

## Adding a new experiment

Copy the closest existing directory (start from one of the `spain-shot/`
variants if you want hardware events; from `forever/` for a quiet
long-running scenario), edit the `Layout` / `ExperimentDefinition` /
`Experiment`, and update its `kustomization.yaml`. For a UC-style
parameter sweep, follow the `uc<N>-…` README's "TUS parameter coverage"
convention so the run-id matrix stays consistent.

**Don't forget the namespace label.** The `Namespace` YAML must carry
`labels.yass-namespace: "true"` — otherwise the operator's
namespace controller ignores it and no per-experiment infrastructure
(messaging broker, executor, mqtt2prom, …) is created. See the main
[README's "Namespace requirement"](../README.md#namespace-requirement)
section for the full reasoning.
