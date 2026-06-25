# small-experiment

Laptop-sized TUS experiment for a single-node kind cluster (~8 vCPU / 22 GB).
A small constellation with global ground coverage, producing files fast enough
to watch traffic flow on the web-ui globe.

- **13 satellites** in an evenly-distributed Walker constellation: RAAN and mean
  anomaly are each stepped by 360/13 ≈ 27.69° so orbital planes and in-plane
  phases are uniform; common inclination 60° on a near-circular ~1200 km shell.
  On the `nano-sat` profile (200m / 256Mi) — the TUS engine is tiny, so a
  satellite pod is light.
- **7 ground stations** — the ESA ESTRACK core network: Cebreros (ES),
  Malargüe (AR), Kourou (GF), New Norcia (AU), Redu (BE), Santa Maria (Azores),
  Kiruna (SE). They use the lean `ground-station-small` profile (250m / 1Gi,
  defined inline in the manifest) instead of `ground-station-hwdef`
  (1500m / 4Gi, sized for EDFS/kubo): this is TUS-only and TUS receive is light,
  so 7 GS fit alongside the 13 sats.
- TUS engine only.

## Scenario

- Every satellite runs `yass-agent-periodic` and creates a **15 MB** file every
  **~20–32 s** — large enough that each sat→GS transfer stays visible on the
  globe for ~30 s. `CHECK_INTERVAL_SECONDS` is staggered (20..32s) so production
  does not fire in lock-step, and paced to roughly keep up with delivery
  (~4 Mbps) rather than pile up backlog on the 8Gi sat disk.
- Every satellite carries a **recurring non-`Destroy` hardware-fault stream**
  (mean interval 5m, jitter 50%, duration 30s ± 50%), rotated across the
  constellation: `NetworkBandwidthReduced` → `NetworkFailure` → `DiskFull` →
  `DiskFailure`, with a per-sat seed for reproducibility.
- Ground stations run `yass-agent-receive-only`: no production, no faults. They
  set `SUCCESS_AFTER_FILES: "1000000"` so they **never complete** and keep
  receiving indefinitely — transfers keep flowing for the live web-ui view
  instead of stopping after the first delivered file.
- `maxDuration: 999999h` — open-ended; tear down when done.

## Prerequisites

The cluster-scoped `nano-sat` HardwareDefinition must exist (once per
environment); `ground-station-small` is bundled in the manifest.

```shell
kubectl apply -f ../_common_/hardware_specs.yaml
```

## Apply

```shell
kubectl apply -f small-experiment.yaml
```

Watch it come up:

```shell
kubectl -n small-experiment-tus get experiment,fsnode
kubectl -n small-experiment-tus get pods -w
```

Open the globe (port-forward the web-ui):

```shell
kubectl -n small-experiment-tus port-forward svc/web-ui 8088:80
# then browse http://127.0.0.1:8088
```

## Resource budget (rough)

20 FsNode pods: 13 sats (`nano-sat` 200m/256Mi) + 7 GS (`ground-station-small`
250m/1Gi), plus the experiment infra (messaging, executor, metrics-bridge,
web-ui …). Sat + GS + infra ≈ 6.3 of 8 vCPU — fits a single 8-vCPU / 22 GB kind
node with headroom for the kubelet/runtime at cold start.

## Delete

Delete the `Experiment` first so the operator's finalizer cleans up the
FsNodes, then delete the namespace for a clean slate:

```shell
kubectl -n small-experiment-tus delete experiment small-experiment-experiment
kubectl delete ns small-experiment-tus
```

> Re-applying repeatedly onto the **same** namespace can leave terminated
> "zombie" pods behind that latch a FsNode `phase=Errored` onto otherwise
> healthy, producing pods. If you see random sats Errored after a redeploy,
> delete the namespace and re-apply from scratch rather than restarting the
> operator.
