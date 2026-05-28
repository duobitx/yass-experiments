# UC1 — Rapid Disaster Response

## Abstract

A single Earth-Observation satellite captures one high-value image at a known
moment and we measure how quickly that file reaches **at least one** ground
station, under varying constellation sizes, file sizes, priorities and (for
EDFS) replication factors. Compares the distributed file system **EDFS**
against the legacy store-and-forward baseline **TUS**.

## Detailed description

One designated satellite acts as the producer: on activation its agent writes
a single image of size `S` MB with priority `P` and exits. Every other
satellite in the constellation is idle ("pure relay"). The seven ESTRACK
ground stations are fixed and act as receivers. The experiment ends as soon
as the first GS reports a `RECEIVED` event for that file (`END_ON_ANY=true`
on every GS agent).

The producing satellite is positioned far from any ground station at `t=0` so
the file cannot be downlinked instantly — the time advantage of EDFS comes
from peer satellites that happen to be in LOS with both the producer (via
ISL) and a GS earlier than the producer itself would.

Each `(engine, S, P, sat_count, RF)` combination is one run with its own
`Experiment.spec.runId` so dashboards can multi-select across them.

## Main goal

Quantify how **time-to-first-delivery** scales for EDFS vs. TUS as a function
of constellation size and file size. The hypothesis is:

- EDFS time-to-first-delivery decreases sub-linearly as `sat_count` grows
  (more relay paths → more chances of an earlier hand-off).
- TUS time-to-first-delivery is largely insensitive to `sat_count` (the file
  only exists on the producer until it meets a GS).
- High-priority files (UC1 also overlaps with UC3 here) should arrive
  at least as fast as normal, ideally faster on EDFS.

KPI: median EDFS time-to-first-delivery at `sat_count ≥ 8` is **≤ 50% of
TUS** at the same `sat_count` and the same `S`.

## Parameters

| Parameter                 | Values                   | Notes                                                                                                                    |
|---------------------------|--------------------------|--------------------------------------------------------------------------------------------------------------------------|
| `engine`                  | `tus`, `edfs`            | TUS is the baseline.                                                                                                     |
| `S` (file size)           | `8M`, `128M`, `1G`       | Spans the regimes "fits in one pass", "needs several passes", "large transfer".                                          |
| `priority`                | `low`, `default`, `high` | Set via the producer agent's `FILE_PRIORITY` env, written to `.priority` so `fs_engine_wrapper` attaches it to the file. |
| `sat_count`               | 1, 2, 8, 21, 55          | Walker-like sweep; "1" is a degenerate single-satellite control.                                                         |
| `RF` (replication factor) | 1, 3, 5 — **EDFS only**  | TUS has no replication concept; for TUS runs `RF` is omitted.                                                            |
| `gs_count`                | fixed at 7 (ESTRACK)     | Constant across the sweep to keep the headline result a one-dimensional EDFS-vs-TUS comparison.                          |
| `max_duration`            | `2h`                     | Far longer than any plausible EDFS delivery; cuts off pathological TUS runs.                                             |

**TUS parameter coverage.** TUS has no notion of file priority or
replication factor — `priority` and `RF` are ignored by the engine.
For TUS we therefore only sweep `(S, sat_count)`, pin `priority=default`
and omit `RF` from the run-id. Each `(S, sat_count)` TUS run is then
compared against the full `(priority, RF)` matrix of EDFS runs at the
same `(S, sat_count)`.

Each combination produces one overlay directory plus one
`Experiment.spec.runId`:

- EDFS: `uc1-edfs-S<size>-P<priority>-N<sat_count>-RF<rf>`
- TUS:  `uc1-tus-S<size>-N<sat_count>`

## Additional metrics

Beyond the headline `yass_file_delivery_seconds_bucket` histogram (filtered
by source = the producer):

- Per-fsNode and aggregate:
  - `yass_container_cpu_millicores`
  - `yass_container_memory_bytes`
  - `yass_network_tx_bytes_total`, `yass_network_rx_bytes_total`
- Bandwidth efficiency: `bytes_transmitted / file_size`. A pure TUS
  bounce-and-forward should sit close to 1.0; high-RF EDFS will be > 1.
- Total network cost: `sum(yass_network_tx_bytes_total)` aggregated over
  the whole constellation for the run. Plain bytes shipped — engine-
  agnostic, no made-up coefficients. Used as the proxy for "how much did
  this engine cost us in total" when comparing EDFS vs TUS.

