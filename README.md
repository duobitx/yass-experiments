# yass-experiments

Experiment manifests applied to a YASS cluster (kind locally or the remote ESA cluster). Each experiment is a kustomize overlay producing a `Layout`, an `ExperimentDefinition` and an `Experiment` that the [yass-operator](../yass-simulator/yass-operator/) reconciles into FsNode pods.

## Layout

- [`experiments/`](./experiments/) — the experiments themselves; see its README for the index.
- [`experiments/_common_/`](./experiments/_common_/) — shared `HardwareDefinition` set referenced by every layout via `hardwareSpecRef`.
- `create-experiment-namespace.sh` — helper that creates a namespace already labeled `yass-namespace=true` (required by the operator's namespace controller).
- `fsNode0.yaml` — minimal standalone `FsNode` for manual smoke tests.

## Apply

```shell
kubectl apply -k experiments/<name>
kubectl delete -k experiments/<name>
```

The operator does the rest: per-experiment infrastructure (messaging broker, experiment-executor, events-webapp, web-ui) plus one Pod per FsNode.
