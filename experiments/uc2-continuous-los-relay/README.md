# UC2 — Continuous LOS Relay

> **Status — specification only.** Unlike [UC1](../uc1-rapid-disaster-response/),
> this use case is not yet scaffolded: there are no `_layouts/`, `_template/`,
> `run.sh` or `tiers.yaml`, so it cannot be executed as-is. The sections below
> are the agreed design (parameters, KPI, metrics). To build the runnable
> overlays, mirror UC1's layout and generate the per-`sat_count` layouts from
> the shared OneWeb roster:
>
> ```shell
> python3 ../_common_/regenerate-uc-layouts.py \
>     --target-dir _layouts --name-prefix uc2
> ```

## Abstract

Every satellite produces exactly one file at the start of the run. While the
constellation is operating, we inject a **continuous, randomised stream of
hardware faults** (network failures, bandwidth reductions, disk full / disk
failure) on satellites and ground stations alike. The experiment measures
how long it takes for **every** produced file to reach at least one ground
station, and which fraction never makes it inside the time budget. Compares
EDFS against TUS.

## Detailed description

At `t=0` every satellite agent writes one file of fixed size and exits — so
the number of distinct files in flight is `sat_count`. The seven ESTRACK
ground stations run a multi-receiver agent that keeps logging RECEIVED
events for every distinct file it sees and stays alive until the experiment
ends.

A per-fsNode hardware-event schedule is generated up front and stamped into
each `Behaviour.hardwareEvents` so the run is fully reproducible (the
operator's CRD validation already ensures `Destroy` is rejected for
recurring schedules; we use only the non-terminal fault types). Default
fault intensity: on every fsNode, on every fault type, one occurrence
every `5m` (intervalJitterPercent: 50) lasting `30s-60s`
(durationJitterPercent: 50). This produces "roughly 10-15% of fsNodes in
some kind of fault state at any given moment" — degraded but not crippled.

Fault menu (no `Destroy` here; that's UC4's domain):

- `NetworkBandwidthReduced` with `capBitsPerSec: 100 kbps`
- `NetworkFailure` (full link drop)
- `DiskFull`
- `DiskFailure`

The run ends when (a) all files are received by at least one GS, or (b) the
`maxDuration` (default `6h`) timer fires — whichever comes first. The
latter case is itself a data point.

## Main goal

Quantify **delivery completeness and time-to-all-delivered under realistic
operational faults**. The hypothesis is:

- EDFS achieves ≥ 95% delivery success rate even under the fault stream,
  because the DHT replicates each file across multiple peers and any of
  those replicas can be the one that meets a GS.
- TUS degrades faster: a fault on the producer's link during its only
  contact window with a GS loses that file for the whole run.

KPI: **delivery success rate ≥ 95% for EDFS** at every sat_count; TUS is
expected to fall below.

## Parameters

| Parameter         | Values                                              | Notes                                                                                                             |
|-------------------|-----------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `engine`          | `tus`, `edfs`                                       | Both engines are tested under the same fault schedule.                                                            |
| `priority`        | `low`, `default`, `high`                            | Set via the producer agent's `FILE_PRIORITY`.                                                                     |
| `sat_count`       | 1, 2, 8, 21, 55                                     | Same Walker-like sweep as UC1.                                                                                    |
| `RF`              | 1, 3, 5 — **EDFS only**                             | Probes "is more replication actually helpful here?"                                                               |
| `gs_count`        | fixed at 7 (ESTRACK)                                |                                                                                                                   |
| `file_size`       | `32M`                                               | One regime per UC2 run; we sweep RF/sat_count rather than file size to keep the deliverable set focused.          |
| `fault_intensity` | mean interval `5m`, jitter `50%`, duration `30-60s` | Encoded directly in each behaviour's `hardwareEvents.schedule`; the run-id includes a `seed` for reproducibility. |
| `max_duration`    | `6h`                                                | Tight enough that "still in flight at the deadline" actually counts against the success rate.                     |

**TUS parameter coverage.** TUS has no notion of file priority or
replication factor — `priority` and `RF` are ignored by the engine.
For TUS we therefore only sweep `(sat_count)` (with `fault_intensity`
held to the same per-run schedule), pin `priority=default` and omit
`RF`. Each TUS run is compared against the full `(priority, RF)`
matrix of EDFS runs at the same `sat_count` and the same fault seed.

## Additional metrics

Beyond `yass_file_delivery_seconds` histogram (filtered to "any-GS
delivery"):

- Delivery success rate:
  `count(distinct files received by ≥1 GS) / sat_count`.
- "Critical replication time" — wall-clock at which the
  `delivery_success_rate` reaches each of `{50%, 90%, 95%, 100%}`.
- Per-fsNode and aggregate:
  - `yass_container_cpu_millicores`, `yass_container_memory_bytes`
  - `yass_network_tx_bytes_total`, `yass_network_rx_bytes_total`
- Fault overlay (for cross-correlation, not a KPI):
  `yass_hardware_event_active{type}` from the timeline dashboard so we can
  see which files happen to be in flight during which fault windows.
- Total network cost: `sum(yass_network_tx_bytes_total)` aggregated over
  the whole constellation for the run. Plain bytes shipped — engine-
  agnostic, no made-up coefficients. Useful for comparing the relative
  cost of EDFS replication vs. TUS bounce-and-forward under the same
  fault stream.
