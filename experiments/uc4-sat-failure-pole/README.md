# UC4 — Sat Failure

## Abstract

A single satellite captures one high-priority image while flying over a
pole (far from any ESTRACK ground station). Shortly afterwards — and
**before** the satellite has any line-of-sight with any GS — the
satellite is killed by a `Destroy` hardware event. We then watch whether
the image still reaches the ground via peer satellites. Run on both
engines: EDFS is expected to deliver via inter-satellite links; TUS is
expected to lose the file entirely.

## Detailed description

The producer satellite is placed on an orbit whose initial position at
`t=0` is high-latitude and out of LOS with every ESTRACK station. The
producer's agent writes one file of size `S`, with priority `high`, and
exits. Roughly `T_destroy` seconds after the photo is taken — but **always
before** the producer's first contact window with any GS — a `Destroy`
hardware event fires on the producer, irreversibly taking it offline.

For EDFS, success requires that the producer has had enough time (between
the photo and the destroy) to replicate at least one block of the file to
at least one surviving peer satellite, which then carries it forward
until a GS comes into LOS. For TUS, the file existed only on the producer
and the run becomes a control: TUS should fail to deliver, demonstrating
the loss-mode that EDFS is supposed to mitigate.

The other satellites in the constellation do nothing besides act as
relays; the seven ESTRACK ground stations all have `END_ON_ANY=true` so
the first RECEIVED terminates the run.

## Main goal

Demonstrate that **EDFS survives the loss of the file's original
producer** for an image with no remaining copies, and quantify the
trade-off:

- Binary outcome: did the file reach a GS? (`delivered ∈ {true, false}`)
- Conditional metric: when it does reach a GS, what was
  `time-to-first-delivery`?
- Comparison: `EDFS.delivered = true && TUS.delivered = false` on every
  `(sat_count, T_destroy)` combination.

KPI:

- EDFS delivery success rate on UC4 is **≥ 80%** at `sat_count ≥ 8`.
- TUS delivery success rate on UC4 is **0%** by construction.

## Parameters

| Parameter             | Values                                                        | Notes                                                                                                                      |
|-----------------------|---------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `engine`              | `tus`, `edfs`                                                 | TUS run is the negative-control baseline.                                                                                  |
| `sat_count`           | 1, 2, 8, 21, 55                                               | At `sat_count = 1` even EDFS must fail — no peers to replicate to. That is itself a useful data point.                     |
| `T_destroy`           | `5m`, `15m`, `45m`                                            | Seconds (well, minutes) between "photo taken" and "Destroy fires". Smaller `T_destroy` means tighter race for replication. |
| `S` (file size)       | `32M`                                                         | Single regime; we focus the sweep on sat_count × T_destroy.                                                                |
| `priority`            | fixed at `high`                                               | UC4 is the canonical high-priority scenario.                                                                               |
| `RF` (EDFS only)      | fixed at 3                                                    | A single RF keeps the headline result interpretable.                                                                       |
| `gs_count`            | fixed at 7 (ESTRACK)                                          |                                                                                                                            |
| `simulationStartTime` | tuned per `sat_count` so the producer is over a pole at `t=0` | Critical: the whole setup hinges on the producer being out of LOS at photo time.                                           |
| `max_duration`        | `4h`                                                          | TUS will run out of budget; EDFS should deliver in well under that.                                                        |

**TUS parameter coverage.** `priority` (fixed at `high`) and `RF` are
both ignored by TUS. The TUS sweep therefore reduces to `(sat_count,
T_destroy)`; `RF` is omitted from the TUS run-id entirely. This is by
design — UC4 measures binary "does the file survive the producer's
loss?", and on TUS that answer is "no" for every parameter combination,
so a single run per `(sat_count, T_destroy)` suffices as the negative
baseline.

## Additional metrics

Beyond `yass_file_delivery_seconds`:

- `delivery_success` boolean: `count(file_delivered events) ≥ 1` per run.
- For EDFS, the **fragmentation state at the moment of Destroy**:
  `yass_edfs_replica_completeness{cid, fsNode}` sampled at the
  `HardwareDestroyActive` k8s event timestamp. If the producer hadn't
  managed to replicate any blocks before being destroyed, the run will
  fail and this snapshot will explain why.
- "Survival snapshot" per fsNode at `T_destroy`:
  `yass_volume_used_bytes`, `yass_container_memory_bytes` for every node
  alive at that instant.
- For TUS, "what would have delivered" — total bytes the producer managed
  to push out before Destroy, so we can quantify the loss volume.
- Total network cost: `sum(yass_network_tx_bytes_total)` aggregated over
  the whole constellation. For successful EDFS runs this tells us "how
  expensive was the rescue" — i.e. how many bytes the constellation had
  to shuffle through ISLs to compensate for the producer's loss.
