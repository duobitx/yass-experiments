# Experiments

Each subdirectory is a self-contained experiment that can be applied with `kubectl apply -k <dir>`.

## Index

- [`forever/`](./forever/) — long-running constellation (3 satellites + 7 ESTRACK ground stations) used to exercise sustained data flow over the simulated network. Has [`edfs/`](./forever/edfs/) and [`tus/`](./forever/tus/) variants on top of a shared [`base/`](./forever/base/) layout.
- [`networking-demo/`](./networking-demo/) — minimal connectivity demo (3 satellites + 1 ground station, `fs_engine_udp_ping` engine). Verifies that `world-controller` actually shapes traffic with tc.
- [`_common_/`](./_common_/) — shared `HardwareDefinition` set; not an experiment, included as a base.

To add a new experiment, copy an existing directory, edit the `Layout` / `ExperimentDefinition` / `Experiment`, and update its `kustomization.yaml`.
