# UC5 — General Failure (EDFS only)

## Abstract

Twenty per cent of the constellation produces a small, finite batch of
images each. The whole constellation — both the producing satellites and
every ground station — is hit by a continuous stream of **non-terminal**
hardware faults (no `Destroy`). We measure how long it takes for every
produced image to reach **at least one** ground station, and check whether
EDFS is eventually able to deliver them all in spite of the fault stream.
EDFS only; TUS is out of scope here.

UC5 sits between UC2 (every sat produces one file, faults active) and UC4
(one terminal failure on one producer). It's the long-duration stress test
of EDFS's eventual consistency under sustained but non-catastrophic
degradation.

## Detailed description

Out of the `sat_count` satellites in the constellation, exactly **20%**
are designated **producers**. Each producer's agent writes `N=5` images
spaced by `IMAGE_INTERVAL` (default `2m`), then exits. The total number
of distinct files in flight per run is therefore `floor(0.2 * sat_count)
* 5` — finite and countable, as required.

While the run is in progress, the operator stamps a per-fsNode random
fault schedule on every fsNode (producers, relays and ground stations
alike). Allowed fault types: `NetworkBandwidthReduced`, `NetworkFailure`,
`DiskFull`, `DiskFailure`. **`Destroy` is explicitly excluded** — the
whole point of UC5 is to study eventual delivery under transient
degradation; a destroyed producer of a file with `RF=1` (no copies yet)
would be UC4's loss-mode and would muddy the metric.

The seven ESTRACK ground stations receive on all files (their agent does
not exit on first RECEIVED — it keeps logging until the run ends).

The run ends when (a) every file has been received by at least one GS, or
(b) `maxDuration` fires. Both outcomes are reportable.

## Main goal

Show that **EDFS eventually delivers every file under sustained
non-catastrophic faults**, and quantify "how long is eventual?":

- `delivery_success_rate = files_received_by_any_gs / files_produced`.
  Target ≥ 95%.
- Time-to-all-delivered when the run terminates by (a).
- The trade-off between `RF` and total energy / network spent: higher
  `RF` should improve `delivery_success_rate` at the cost of more
  cross-talk on inter-satellite links.

KPI:

- `delivery_success_rate ≥ 95%` at `RF ≥ 3` for every tested
  `sat_count`.
- Time-to-all-delivered grows sub-linearly with the number of files in
  flight.

## Parameters

| Parameter            | Values                                              | Notes                                                                                                                                                                                                      |
|----------------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `engine`             | `edfs` only                                         | TUS would simply lose files under faults; out of scope.                                                                                                                                                    |
| `sat_count`          | 1, 2, 8, 21, 55                                     | `sat_count=1` degenerates to "one producer, no relays" — a sanity-only data point.                                                                                                                         |
| `producer_fraction`  | fixed at 20%                                        | `producers = max(1, floor(0.2 * sat_count))`. For `sat_count=1` and `sat_count=2` the floor takes us to 1; for `sat_count=8` it's 1 (floor(1.6)=1); for `sat_count=21` it's 4; for `sat_count=55` it's 11. |
| `files_per_producer` | 5                                                   | Bounded, so we can compute completion rate cleanly.                                                                                                                                                        |
| `image_interval`     | `2m`                                                | Spreads file births in time so the network has work to do throughout the run.                                                                                                                              |
| `file_size`          | `8M`                                                | Small enough that several files can be in flight simultaneously without strangling the bandwidth model.                                                                                                    |
| `RF`                 | 1, 3, 5                                             | The headline EDFS knob.                                                                                                                                                                                    |
| `priority`           | `default`                                           | Priority handling is UC3's question, not UC5's.                                                                                                                                                            |
| `gs_count`           | fixed at 7 (ESTRACK)                                |                                                                                                                                                                                                            |
| `fault_intensity`    | mean interval `5m`, jitter `50%`, duration `30-60s` | Same recipe as UC2, encoded directly in each behaviour's `hardwareEvents.schedule` with a per-run `seed`.                                                                                                  |
| `max_duration`       | `8h`                                                | Long-duration stress test by design.                                                                                                                                                                       |

## Additional metrics

Beyond `yass_file_delivery_seconds`:

- `delivery_success_rate` — explicit Prometheus query:
  `sum(yass_file_received_total{is_target_gs="true"}) /
   sum(yass_file_produced_total)`.
- "Eventual completion" plot: cumulative `delivery_success_rate` as a
  function of wall-clock time. We expect a smooth monotonic climb that
  asymptotes at or close to 1.0 for `RF ≥ 3`.
- `yass_edfs_replica_completeness` heatmap per CID — over a long
  stress-test run we expect "full replicas" to grow steadily as DHT
  pickups absorb each file.
- Per-fsNode resource cost over the long run:
  `yass_container_cpu_millicores`, `yass_container_memory_bytes`,
  `yass_network_tx_bytes_total`.
- Total network cost: `sum(yass_network_tx_bytes_total)` aggregated over
  the whole constellation. Headline trade-off metric for the `RF` sweep —
  the byte volume should grow roughly linearly with `RF` while the
  completion time should drop.
- Fault-overlay correlation: `yass_hardware_event_active` as a state
  timeline so we can visually correlate fault windows with stalled
  per-file progress.
