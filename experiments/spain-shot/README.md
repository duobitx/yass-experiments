# spain-shot

Single-shot delivery experiment: `oneweb-0008` takes one photo when it
crosses Spain (Madrid AOI, radius 500 km), and the file is delivered to
`estrack-new-norcia`.

The photo is `FILE_SIZE` bytes of incompressible pseudo-random content
(blake2s-chained chunks — see [`periodic-agent`](../../../yass-agents/periodic-agent/)).

## Tunables (on the producer behaviour, `oneweb-0008`)

| Env             | Default | Meaning                                                       |
|-----------------|---------|---------------------------------------------------------------|
| `FILE_SIZE`     | `2G`    | Photo size. Suffixes `M`/`G`/`T`.                             |
| `PHOTO_TARGETS` | `spain:40.4:-3.7:500` | AOI (lat/lng/radius_km).                            |
| `MAX_PHOTOS`    | `1`     | Stop producing after this many. Agent stays alive afterwards. |

## Layout (`base/`)

- 1 satellite: `oneweb-0008` (`sentinel-2` hardware).
- 7 ESTRACK ground stations: `new-norcia`, `kiruna`, `redu`, `cebreros`,
  `santa-maria`, `kourou`, `malargue`.

All 7 ground stations exist in the world (so the network topology is
realistic). Only `estrack-new-norcia` is configured as the delivery
target — see each engine variant below.

## Variants

- [`tus/`](./tus/) — TUS engine. `GROUND_STATIONS=estrack-new-norcia`
  pins the delivery to that single GS. Namespace `spain-shot-tus`.
- [`edfs/`](./edfs/) — EDFS engine. Bootstrap peer set to
  `estrack-new-norcia` and `EDFS_REPLICATION_FACTOR_{MIN,MAX}=1` so the
  single pin lands on the bootstrap node. Namespace `spain-shot-edfs`.

## Apply

```shell
kubectl apply -k ./tus    # or ./edfs
```

## Termination

All ground stations run `receive-only-agent` with `END_ON_ANY=true`.

- The first GS to discover the file publishes `{node, file}` to MQTT
  topic `agents/receive-only-agent` and `AgentExperimentEndRequest{SUCCESS}`
  to `experiment/end-request`, then exits with code 0.
- The other 6 GSes subscribe to `agents/receive-only-agent` and exit
  with code 0 as soon as they see that notification.
- `experiment-executor` picks up the end-request and flips the
  `Experiment` to Success.

`maxDuration: 6h` is the upper bound — the experiment normally
finishes much sooner, on the first delivery.

## Notes

- EDFS replication 1-1 + bootstrap-on-target is a best-effort routing
  knob, not a hard contract: IPFS-Cluster may still pin elsewhere if
  the bootstrap pod is unreachable when the producer PUTs. Watch
  `target_fsNode` in `yass_file_delivery_seconds` to confirm.
- `oneweb-0008` is on a polar orbit (TLE inclination ≈ 87.9°). It
  crosses the Madrid AOI roughly once per ~90-minute orbit, so a 6 h
  `maxDuration` gives a few candidate passes.
