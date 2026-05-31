# _common_

Shared resources referenced from every experiment overlay in this repo.

## Resources

- `hardware_specs.yaml` — `HardwareDefinition` objects (`oneweb`, `sentinel-2`, `ground-station-hwdef`, …) referenced by every layout via `hardwareSpecRef:`.
- `oneweb-roster.yaml` — frozen 60-satellite OneWeb TLE roster used to generate the UC* sweep layouts. Owned by the UCs and intentionally independent of the `big-scale` experiment, so the two evolve separately.
- `regenerate-uc-layouts.py` — generator that turns the roster + the spain-shot ESTRACK ground stations into each UC's `_layouts/n*.yaml` via plane-diverse round-robin. Stdlib-only; run with `--target-dir <uc>/_layouts --name-prefix <uc>`.

## Usage

In a per-experiment kustomization, either include this overlay in the bases, or expect it to be applied separately via [`yass-flux/clusters/base/experiments-setup`](../../../yass-flux/clusters/base/experiments-setup/).
