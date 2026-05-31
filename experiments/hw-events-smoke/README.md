# hw-events-smoke

Deterministic smoke test of the `HardwareEvent` mechanism. One satellite
fires every one of the 5 fault types in sequence; the goal is to confirm
the events **activate / clear** correctly and that each fault has its
documented effect on the agent + fs-engine containers.

This is a mechanism test, **not** a delivery scenario — it does not care
whether a file reaches the ground station. Hardware events fire on a
fixed `startOffset` from `t=0`, independent of orbital line-of-sight.

## Layout (`base/`)

- 1 satellite `oneweb-0008` (`sentinel-2` hardware) — the producer and
  the only faulted node.
- 1 ground station `estrack-kiruna` (`ground-station-hwdef`) — receiver,
  gives the satellite some real LOS windows so the network faults have
  visible traffic to act on.

## Producer behaviour

`yass-agent-periodic` in periodic mode (no `PHOTO_TARGETS`): writes a
`4M` file to `/mnt/transfer` every `20s`, so disk/network faults always
land on an active write.

## Event timeline (one-shot, on `oneweb-0008`)

| `startOffset` | Type                      | `duration` | Expected effect                                              |
|---------------|---------------------------|------------|--------------------------------------------------------------|
| `60s`         | `NetworkBandwidthReduced` | `30s`      | Throughput capped at 50 kbit/s — TX/RX rate drops.           |
| `120s`        | `NetworkFailure`          | `30s`      | Engine + agent link dead — transfers stall, no peers.        |
| `180s`        | `DiskFull`                | `30s`      | Writes fail `ENOSPC`; reads still succeed.                   |
| `240s`        | `DiskFailure`             | `30s`      | Every disk I/O fails `EIO` (read **and** write).             |
| `300s`        | `Destroy`                 | terminal   | Engine + agent containers killed, never restarted.           |

Events are spaced `60s` apart with `30s` durations, so no two of the same
type are ever active at once (the operator would drop an overlapping
occurrence). `Destroy` is last — the CRD's CEL rejects any later event on
the same node.

`maxDuration: 8m` gives margin past the last event for observation, then
the run ends on the duration cap (no early `END_ON_ANY`).

## Apply

```shell
kubectl apply -k ./tus
```

Namespace: `hw-events-smoke-tus`.

## How to verify

Primary signal — every transition is published as a Kubernetes Event on
the `FsNode` and on MQTT topic `hardware-events/oneweb-0008`:

```shell
# 4 activate + 4 clear + 1 Destroy-activate = 9 events, at the times above
# (the CRD has no `fsn` short name — use the full `fsnode`)
kubectl -n hw-events-smoke-tus describe fsnode oneweb-0008 | sed -n '/Events:/,$p'
```

Secondary signals:

- `NetworkBandwidthReduced` / `NetworkFailure` — drop in networking
  RX/TX on the node's metrics during the active window.
- `DiskFull` — agent log shows write errors (`ENOSPC` / "No space left").
- `DiskFailure` — engine/agent log shows `EIO` on reads and writes.
- `Destroy` — `kubectl -n hw-events-smoke-tus get pod` shows the engine
  and agent containers `Terminated` and not restarted (`RestartPolicy:
  Never`); the world-controller sidecar stays `Running`.

## Teardown

```shell
kubectl delete -k ./tus
```

DiskFailure leaves a FUSE mount; if the pod hangs `Terminating`, see the
`yass-experiment-delete` flow.
