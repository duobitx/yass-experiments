# UC2NR — Continuous LOS Relay (evenly distributed, no randomness)

A variant of [UC2](../uc2-continuous-los-relay/) with **two** changes and nothing
else:

1. **Even, multi-constellation satellite distribution.** Instead of UC2's single
   OneWeb ~87.9° polar shell (`_common_/oneweb-roster.yaml` + synthetic top-up),
   the satellites are drawn from a mixed-constellation TLE snapshot (Orbcomm,
   Globalstar, Starlink, Iridium-NEXT, OneWeb, Planet — inclinations ~43–97° and
   a range of altitudes) and **even-strided** per `sat_count`, the same recipe as
   [`big-scale`](../big-scale/). This spreads the constellation evenly across
   orbital regimes rather than clustering it in one shell.
2. **No randomness in the fault stream.** The recurring hardware faults keep the
   same menu, mean interval and duration, but the schedule is **fully
   deterministic**: `intervalJitterPercent: 0`, `durationJitterPercent: 0`, no
   per-fsNode seed. To avoid the whole constellation pulsing in and out of fault
   together, each fsNode's `startOffset` is **phased by its index** (10 buckets at
   60…330 s across the 5 m interval), so ~10% of nodes are faulted at any instant
   — UC2's intended fault intensity, but with zero RNG.

Everything else matches UC2 exactly: engines (TUS, EDFS — same images, keys and
`estrack-new-norcia` bootstrap peer), `hardwareSpecRef: oneweb` satellites +
`ground-station-hwdef` GS, the seven ESTRACK ground stations, the success
condition (a GS holding ≥95% of files), the pinned `simulationStartTime`,
`maxDuration: 6h`, and the tier matrix.

## Running

```shell
cd experiments/uc2nr-continuous-los-relay

# Dry-run — render all YAML to _runs/ without applying:
./run.sh --tier all --kubeconfig /path/to/kubeconfig --dry-run

# Full sweep (background, multi-hour):
nohup ./run.sh --tier all --kubeconfig /path/to/kubeconfig \
    > _runs/driver.log 2>&1 &

# Single tier:
./run.sh --tier 1 --kubeconfig /path/to/kubeconfig
```

Rendered manifests land in `_runs/<run_id>/`; artefact bundles in
`_runs/<run_id>.tar.gz`.

RunId convention:

- EDFS: `uc2nr-edfs-p<priority>-n<NN>-rf<rf>`
- TUS:  `uc2nr-tus-n<NN>`

Layouts are referenced as `uc2nr-n<NN>` (cluster-scoped; distinct from UC2's
`uc2-n<NN>`).

## Regenerating the layouts

The `_layouts/` are derived from `tools/tle-snapshot.txt`:

```shell
python3 tools/build-snapshot.py   # fetch a diverse, ≥210-sat snapshot from celestrak
python3 tools/gen-layouts.py      # even-stride per sat_count + 7 ESTRACK GS → _layouts/nNN.yaml
```

`build-snapshot.py` caches each constellation group under `tools/.tle-cache/`
so a transient celestrak rate-limit (HTTP 403) on one group does not discard the
others; delete the cache to force a fresh fetch. `tle-snapshot.txt` is checked in
so the layouts are reproducible without re-fetching.

## Why

UC2's OneWeb shell clusters every satellite in near-identical orbits and its
fault schedule is randomised (only reproducible via seed). UC2NR removes both
confounders so the EDFS-vs-TUS delivery comparison is driven by an even global
constellation under an identical, repeatable fault load — making per-run
differences attributable to the engines, not to roster clustering or RNG.

See [UC2's README](../uc2-continuous-los-relay/README.md) for the full scenario,
success condition, parameters and metrics, all of which carry over unchanged.
