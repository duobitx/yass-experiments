# forever

Long-running experiment (`maxDuration: 999999h`) — a constellation of three satellites (`oneweb-0008`, `yaogan-25c`, `kuiper-00060`) plus seven ESTRACK ground stations (`new-norcia`, `kiruna`, `redu`, `cebreros`, `santa-maria`, `kourou`, `malargue`).

Satellites run [`periodic-agent`](../../../yass-agents/periodic-agent/) (data producer); ground stations run [`receive-only-agent`](../../../yass-agents/receive-only-agent/).

## Layout (`base/`)

The shared base (`base/00_namespace.yaml` + `base/01_layout.yaml`) is reused by both engine variants below.

## Variants

- [`edfs/`](./edfs/) — runs the experiment with the [EDFS](../../../fs-engines/edfs_engine/) (IPFS-Cluster) engine. Namespace `forever-experiment-edfs`. See its README for hands-on commands.
- [`tus/`](./tus/) — runs the experiment with the [TUS](../../../fs-engines/tus_fs_engine/) reference engine. Namespace `forever-experiment-tus`.

## Apply

```shell
kubectl apply -k ./edfs   # or ./tus
```
