# yass-experiments

Experiment manifests applied to a YASS cluster (kind locally or the remote
ESA cluster). Each experiment is a kustomize overlay producing a `Layout`,
an `ExperimentDefinition` and an `Experiment` that the
[yass-operator](../yass-simulator/yass-operator/) reconciles into FsNode
pods.

## Layout

- [`experiments/`](./experiments/) — the experiments themselves; see its
  README for the index. Includes the five canonical use-case directories
  `uc1-..uc5-..` (see "Use cases" below).
- [`experiments/_common_/`](./experiments/_common_/) — shared
  `HardwareDefinition` set referenced by every layout via
  `hardwareSpecRef`.
- [`tools/`](./tools/) — companion CLIs for analysing results:
  `prom-snapshot` (Prometheus range-vector → per-metric-family CSV),
  `yass-export` (bundle events.ods + events-csv/ + metrics-csv/ +
  experiment.yaml into one `<name>-<run_id>.tar.gz` for archival).
- `create-experiment-namespace.sh` — helper that creates a namespace
  already labeled `yass-namespace=true` (required by the operator's
  namespace controller).
- `fsNode0.yaml` — minimal standalone `FsNode` for manual smoke tests.

## Apply

```shell
kubectl apply -k experiments/<name>
kubectl delete -k experiments/<name>
```

The operator does the rest: per-experiment infrastructure (messaging
broker, experiment-executor, events-webapp, web-ui, metrics-bridge,
mqtt2prom) plus one Pod per FsNode.

### Namespace requirement

**The experiment's namespace MUST carry the label
`yass-namespace: "true"`.** The operator's namespace controller
(`namespace_controller.go`) only picks up namespaces with that label
and, on a match, materialises the per-namespace infrastructure
(`docker-secret` for GHCR pulls, `yass-experiment-sa` ServiceAccount,
RoleBinding, finalizer). Without the label nothing happens —
`kubectl apply -k` will succeed quietly but no Pods will ever come
up.

Every `Namespace` YAML in this repo (e.g. `spain-shot/base/00_namespace.yaml`,
the UC overlays) ships with the label inline. If you create a
namespace by hand, use the `create-experiment-namespace.sh` helper or
add the label yourself:

```shell
kubectl label namespace <name> yass-namespace=true
```

## Use cases

Each `uc<N>-…/` subdirectory under `experiments/` is one of the five
canonical use cases. Each has its own `README.md` covering: abstract,
detailed description, main goal, parameters and additional metrics.

| UC | Headline | Engines |
|---|---|---|
| [UC1 — Rapid Disaster Response](./experiments/uc1-rapid-disaster-response/) | One producer, time-to-first-delivery as a function of constellation size, file size and priority. | EDFS + TUS |
| [UC2 — Continuous LOS Relay](./experiments/uc2-continuous-los-relay/) | Every sat produces one file under a continuous random fault stream; delivery success rate ≥ 95%. | EDFS + TUS |
| [UC3 — Priority-Aware Routing](./experiments/uc3-priority-aware-routing/) | Same scenario at low/default/high priority — does EDFS actually privilege high-priority files? | EDFS only |
| [UC4 — Sat Failure (extra)](./experiments/uc4-sat-failure-pole/) | Producer takes a polar shot and is destroyed before its first LOS with a GS — does EDFS still deliver via ISL? | EDFS + TUS (TUS as negative control) |
| [UC5 — General Failure (extra)](./experiments/uc5-general-failure/) | 20% of sats each produce 5 files under a continuous non-`Destroy` fault stream; eventual-consistency stress test. | EDFS only |

### Parameter sweep convention

Every UC parametrises its runs by some subset of `{engine, sat_count,
file_size, priority, RF, T_destroy}`. The mapping is
written down in each UC's `Parameters` table. Two cross-cutting
conventions to know:

- **GS count is fixed at 7 (ESTRACK)** across all UCs. Sweeps vary
  `sat_count` and engine knobs, never the ground segment.
- **TUS ignores `priority` and `RF`.** On UCs that run both engines, the
  TUS sweep degenerates accordingly — see the "TUS parameter coverage"
  note in each UC's `Parameters` section.

### Run identification

Every Experiment carries an explicit
[`spec.runId`](../yass-simulator/yass-operator/api/v1/experiment_types.go)
(observability-v2-spec.md §G2). The run-id pattern per UC follows the
template `uc<N>-<engine>-<encoded sweep parameters>`; this is what
ends up as the `run_id` label on Prometheus metrics and as a directory
hint in `yass-export` bundles, so a flat list of artefacts stays
unambiguous across UCs and clusters.

## Deliverables

For every run we keep:

- Grafana screenshots / exports — typically yass-overview,
  yass-timeline (and yass-edfs-fragmentation for EDFS).
- Raw metrics: CSV (via `tools/prom-snapshot/`) or `.ods` (via
  `events-exporter`'s ODS mode + `tools/yass-export/`).
- Short written summary: setup, parameters, observations, KPI verdict.
- The original `Experiment` YAML, so the run is re-runnable verbatim.

The `tools/yass-export/yass-export.sh <namespace>` wrapper bundles all
of the above into a single `<name>-<run_id>.tar.gz` keyed to the
run-id, ready to be copied to long-term storage.
