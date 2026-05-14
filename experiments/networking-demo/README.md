# networking-demo

Smallest end-to-end experiment in the repo — wires three satellites (`oneweb-0008`, `yaogan-25c`, `kuiper-00060` — the last intentionally out of range of New-Norcia) and one ground station (`new-norcia`) through the [`fs_engine_udp_ping`](../../../fs-engines/fs_engine_udp_ping/) engine to demonstrate that the `world-controller` applies tc rules and packets actually get shaped/dropped by the simulated network.

All agents are `yass-agent-sleep` — no real workload, the experiment is purely about connectivity.

## Files

- `00_namespace.yaml` — namespace `networking-demo`.
- `01_custom-hardware_specs.yaml` — custom hardware specs for this experiment.
- `02_layout.yaml` — `Layout/networking-demo-layout` with TLEs for the three satellites and the lat/lng for New-Norcia.
- `03_experiment_defintion.yaml` — `ExperimentDefinition/networking-demo-experimentdef` (`maxDuration: 1h`).
- `04_experiment.yaml` — `Experiment` with engine container `ghcr.io/duobitx/yass-fs_engine_udp_ping:latest`, `HOST=new-norcia`, `start: true`.

## Apply / remove

```shell
kubectl apply  -k experiments/networking-demo
kubectl delete -k experiments/networking-demo
```
