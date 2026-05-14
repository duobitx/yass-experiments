# _common_

Shared resources referenced from every experiment overlay in this repo.

## Resources

- `hardware_specs.yaml` — `HardwareDefinition` objects (`sentinel-2`, `ground-station-hwdef`, …) referenced by every layout via `hardwareSpecRef:`.

## Usage

In a per-experiment kustomization, either include this overlay in the bases, or expect it to be applied separately via [`yass-flux/clusters/base/experiments-setup`](../../../yass-flux/clusters/base/experiments-setup/).
