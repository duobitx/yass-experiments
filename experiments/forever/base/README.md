# forever / base

Shared base for the `forever` experiment variants in `../edfs/` and `../tus/`.

## Resources

- `00_namespace.yaml` — namespace `forever-experiment` (labeled `yass-namespace=true`).
- `01_layout.yaml` — `Layout/forever-layout` with 3 satellites + 7 ESTRACK ground stations.

The engine-specific `ExperimentDefinition` and `Experiment` are layered on top by the sibling overlays.
