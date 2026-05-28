# UC3 — Priority-Aware Routing (EDFS only)

## Abstract

One satellite produces one large file. We re-run the same scenario for each
of the three file priorities (`low`, `default`, `high`) on the same
constellation topology and measure how long it takes the file to reach at
least one ground station. The hypothesis is that the EDFS engine treats
high-priority files preferentially (faster replication, less aggressive
back-off under congestion), so a high-priority file should arrive
**measurably faster** than a low-priority one on otherwise identical runs.

This use case targets only **EDFS** — TUS has no notion of file priority on
the wire, so the comparison is internal to EDFS, not engine-vs-engine.

## Detailed description

A single producer satellite writes one large file. The producer's agent
sets `FILE_PRIORITY` to one of `low`, `default`, `high` and writes a
`.priority` sentinel that `fs_engine_wrapper` propagates into the
file's `Attributes` map (read by EDFS at replication time).

For each constellation size we run three back-to-back experiments — one per
priority value — with deterministic `simulationStartTime` so the orbital
pass schedule and the GS visibility windows are bit-identical between runs.
Anything different that we observe in time-to-first-delivery is then
attributable to the priority handling, not orbital luck.

The seven ESTRACK ground stations are the receivers; each terminates on
the first RECEIVED with the configured `END_ON_ANY=true`.

## Main goal

Establish a quantitative answer to: **does EDFS's priority handling
actually move the needle?** Specifically:

- Time-to-first-delivery of a `high` file is statistically lower than that
  of a `low` file at every sat_count we test.
- The ordering `high < default < low` is preserved at every sat_count
  (monotonic).
- The effect should grow with sat_count: more peers → more chances for the
  priority weighting to win the next-hop selection race.

KPI: at `sat_count ≥ 8`, time-to-first-delivery for `high` is at least
**20% lower** than for `low` (this mirrors the "20% energy reduction"
target CLAUDE.md uses elsewhere — same order of magnitude as the
"interesting effect" threshold).

## Parameters

| Parameter             | Values                                                | Notes                                                                                                                                                        |
|-----------------------|-------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `engine`              | `edfs` only                                           | TUS has no priority semantics — out of scope.                                                                                                                |
| `priority`            | `low`, `default`, `high`                              | The variable of interest.                                                                                                                                    |
| `sat_count`           | 1, 2, 8, 21, 55                                       | Same Walker sweep as UC1.                                                                                                                                    |
| `RF`                  | fixed at 3                                            | A single RF value isolates the priority effect; sweeping both would confound the analysis.                                                                   |
| `gs_count`            | fixed at 7 (ESTRACK)                                  |                                                                                                                                                              |
| `file_size`           | `1G`                                                  | "Large" so the transfer takes long enough for the priority weighting to matter; a 1 MB file would deliver in one bounce and the priority would be invisible. |
| `simulationStartTime` | fixed across the three priority runs of one sat_count | Cancels orbital luck.                                                                                                                                        |
| `max_duration`        | `4h`                                                  | Generous: large file × low priority × no faults could still take a while.                                                                                    |

## Additional metrics

Beyond the headline `yass_file_delivery_seconds` (with the `priority` label
exposed by the producer's agent on its own metrics):

- Throughput envelope: `rate(yass_network_tx_bytes_total)` over the
  producer's run, plotted side-by-side per priority. We expect higher
  effective throughput for `high`.
- Per-priority resource cost: total CPU and memory aggregated per run.
  Important because faster delivery shouldn't come at unreasonable cost.
- Total network cost per priority: `sum(yass_network_tx_bytes_total)`
  over the whole constellation. We expect `high` to ship roughly the same
  byte volume as `low` (the file is the same size); a meaningful
  difference here would mean the priority handling is also affecting
  redundant transmissions, which is worth flagging.
- Block-level pickup rate on EDFS Tier-2 Loki events (`edfs_block`):
  for the same file at different priorities, how quickly does each block
  get pulled by peers? Direct signal of routing preference.
