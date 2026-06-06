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

KPI: **`time_to_first_GS_delivery`** — at `sat_count ≥ 8`,
`first_GS(high) ≤ 0.80 × first_GS(low)` (high-priority delivers at least
**20% faster** than low — meaningful routing-preference, not noise).
Secondary: **priority monotonicity** `first_GS(high) ≤ first_GS(default) ≤
first_GS(low)` at every `sat_count`.

## Parameters

| Parameter             | Values                                                | Notes                                                                                                                                                        |
|-----------------------|-------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `engine`              | `edfs` only                                           | TUS has no priority semantics — out of scope.                                                                                                                |
| `priority`            | `low`, `default`, `high`                              | The variable of interest.                                                                                                                                    |
| `sat_count`           | 1, 2, 8, 21, 100, 200                                 | Same Walker sweep as UC1.                                                                                                                                    |
| `RF`                  | fixed at 3                                            | A single RF value isolates the priority effect; sweeping both would confound the analysis.                                                                   |
| `gs_count`            | fixed at 7 (ESTRACK)                                  |                                                                                                                                                              |
| `file_size`           | `256M` (spec: `1G`)                                   | Executed at **256M**. The UC was specified at `1G` — "large" so the transfer spans many passes and the priority weighting becomes visible (a tiny file delivers in one bounce, hiding priority). At 256M the transfer is ~4× shorter, weakening the priority signal: scope UC3 conclusions to the "256M regime" or re-run at 1G. |
| `simulationStartTime` | fixed across the three priority runs of one sat_count | Cancels orbital luck. Use the same pinned epoch as UC1 (`2026-05-16T23:59:00.000Z`) unless a sat_count needs a different one. |
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

## Running

The full sweep is **18 runs** (6 sat_counts × 3 priorities), all EDFS. Run
time per entry is up to `max_duration=4h`; the total wall-clock budget for
the full sweep is roughly 72 h plus inter-run drain pauses.

```shell
# Dry-run (renders YAML to _runs/, no cluster contact)
./run.sh --tier all --kubeconfig /path/to/kubeconfig --dry-run

# Full sweep on prod cluster
./run.sh --tier all --kubeconfig /path/to/kubeconfig

# Single tier only
./run.sh --tier 1 --kubeconfig /path/to/kubeconfig
```

Outputs per run land in `_runs/<run_id>/` (rendered YAML + run.log) and
`_runs/<run_id>.tar.gz` (yass-export bundle with Prometheus snapshots and
Grafana exports).

### Sat selection

Producer satellite: `oneweb-0027` (RAAN ≈ 10°, first plane-diverse pick).
Layouts were generated by the shared generator — to regenerate:

```shell
python3 ../_common_/regenerate-uc-layouts.py \
    --target-dir _layouts --name-prefix uc3
```

### Run-id convention

`uc3-edfs-p<priority>-n<NN>-rf3` — e.g. `uc3-edfs-phigh-n08-rf3`.
