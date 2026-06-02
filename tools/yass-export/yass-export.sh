#!/usr/bin/env bash
# yass-export — bundle one experiment-run's deliverables into a single
# tar.gz: events.ods + events-csv/ + metrics-csv/ + summary.json (if a
# compare is run) + experiment.yaml.
#
# See yass-docs/observability-v2-spec.md §G5.
#
# Usage:
#   yass-export.sh <namespace> [--out <dir>] [--prom <url>] [--loki <url>]
#
# Requires:
#   - kubectl (with current context targeting the cluster)
#   - events-exporter + prom-snapshot binaries built and on $PATH
#     (or env EVENTS_EXPORTER_BIN / PROM_SNAPSHOT_BIN pointing at them)

set -euo pipefail

ns=${1:-}
if [[ -z "$ns" ]]; then
  echo "usage: $0 <namespace> [--out <dir>] [--prom <url>] [--loki <url>]" >&2
  exit 2
fi
shift

out=""
prom_url="http://prometheus.yass-system.svc:9090"
loki_url="http://loki.yass-system.svc.cluster.local:3100"

while [[ $# -gt 0 ]]; do
  case $1 in
    --out)  out=$2; shift 2 ;;
    --prom) prom_url=$2; shift 2 ;;
    --loki) loki_url=$2; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

events_bin=${EVENTS_EXPORTER_BIN:-events-exporter}
prom_bin=${PROM_SNAPSHOT_BIN:-prom-snapshot}

# Resolve experiment + run_id from the Experiment CR in the namespace.
exp=$(kubectl -n "$ns" get experiment -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -z "$exp" ]]; then
  echo "no Experiment CR in namespace $ns" >&2
  exit 3
fi
run_id=$(kubectl -n "$ns" get experiment "$exp" -o jsonpath='{.spec.runId}' 2>/dev/null || true)
if [[ -z "$run_id" ]]; then
  # Falls back to the operator-auto stamp embedded in pod labels.
  run_id=$(kubectl -n "$ns" get pod -l yass-experiment="$exp" -o jsonpath='{.items[0].metadata.labels.yass-run-id}' 2>/dev/null || echo "$exp-$(date -u +%Y%m%dT%H%M%SZ)")
fi
engine=$(kubectl -n "$ns" get pod -l yass-experiment="$exp" -o jsonpath='{.items[0].metadata.labels.yass-engine}' 2>/dev/null || echo unknown)

if [[ -z "$out" ]]; then
  out=$(mktemp -d -t yass-export-XXXXXX)
fi
mkdir -p "$out"
bundle="$out/$exp-$run_id"
mkdir -p "$bundle/events-csv" "$bundle/metrics-csv"

echo "bundle: $bundle"

# 1. events.ods (human-readable)
"$events_bin" \
  --loki "$loki_url" \
  --experiment "$exp" \
  --run-id "$run_id" \
  --engine "$engine" \
  --out "$bundle/events.ods" \
  --format ods

# 2. events.csv/<kind>.csv (tar.gz, then extract)
csv_tar="$bundle/events.tar.gz"
"$events_bin" \
  --loki "$loki_url" \
  --experiment "$exp" \
  --run-id "$run_id" \
  --engine "$engine" \
  --out "$csv_tar" \
  --format csv
tar -xzf "$csv_tar" -C "$bundle/events-csv/"
rm -f "$csv_tar"

# 3. Prometheus snapshots → metrics-csv/*.csv
"$prom_bin" \
  --prometheus "$prom_url" \
  --experiment "$exp" \
  --run-id "$run_id" \
  --engine "$engine" \
  --window 24h \
  --out "$bundle/metrics-csv"

# 4. Save the live CRs this run depends on, for full re-runnability:
# the namespaced Experiment plus the cluster-scoped ExperimentDefinition,
# Layout and the HardwareDefinitions the Layout references.
mkdir -p "$bundle/manifests"
kubectl -n "$ns" get experiment "$exp" -o yaml > "$bundle/manifests/experiment.yaml"
# Backwards-compatible copy at the bundle root.
cp "$bundle/manifests/experiment.yaml" "$bundle/experiment.yaml"

defref=$(kubectl -n "$ns" get experiment "$exp" -o jsonpath='{.spec.experimentDefRef}' 2>/dev/null || true)
layref=$(kubectl -n "$ns" get experiment "$exp" -o jsonpath='{.spec.layoutDefRef}'     2>/dev/null || true)
[ -n "$defref" ] && kubectl get experimentdefinition "$defref" -o yaml > "$bundle/manifests/experimentdefinition.yaml" 2>/dev/null || true
if [ -n "$layref" ]; then
  kubectl get layout "$layref" -o yaml > "$bundle/manifests/layout.yaml" 2>/dev/null || true
  # HardwareDefinitions referenced by the layout's fsNodes (deduped).
  hwrefs=$(kubectl get layout "$layref" -o jsonpath='{range .spec[*]}{.hardwareSpecRef}{"\n"}{end}' 2>/dev/null | sort -u | grep -v '^$' || true)
  if [ -n "$hwrefs" ]; then
    : > "$bundle/manifests/hardwaredefinitions.yaml"
    for hw in $hwrefs; do
      kubectl get hardwaredefinition "$hw" -o yaml >> "$bundle/manifests/hardwaredefinitions.yaml" 2>/dev/null || true
      echo "---" >> "$bundle/manifests/hardwaredefinitions.yaml"
    done
  fi
fi

# 5. Bundle into tar.gz, and keep the uncompressed directory alongside it
# so results can be browsed without extracting.
final="$out/$exp-$run_id.tar.gz"
tar -czf "$final" -C "$out" "$exp-$run_id"
echo "wrote $final (+ uncompressed dir $bundle)"
