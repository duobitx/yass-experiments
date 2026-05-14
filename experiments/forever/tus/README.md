# forever-experiment-tus

Forever experiment using the TUS reference engine. Namespace `forever-experiment-tus`.

## Apply / remove

```shell
kubectl apply  -k experiments/forever/tus
kubectl delete -k experiments/forever/tus
```

## Files

- `02_experiment_defintion.yaml` — `ExperimentDefinition/forever`: producer satellites run `yass-agent-periodic`, ground stations run `yass-agent-receive-only`.
- `03_experiment.yaml` — `Experiment` referencing the definition above plus the shared `forever-layout` (from [`../base/`](../base/)) and the TUS engine container.
- `kustomization.yaml` — overlay on top of [`../base/`](../base/).

See [`../edfs/README.md`](../edfs/README.md) for parallel hands-on commands; replace `forever-experiment-edfs` with `forever-experiment-tus` and the engine container with the TUS one.
