#!/usr/bin/env bash
# Sequentially run every (n, engine) variant of the scaling experiment.
# For each variant:
#   1. kubectl apply -k …/n{n}/{engine}
#   2. wait until the experiment-executor pod is Ready
#   3. let it soak for $SOAK_SECONDS (default: 6h + 1h drain)
#   4. record the run_id (from the metrics-bridge pod label)
#   5. kubectl delete -k …/n{n}/{engine}, wait for namespace teardown
#
# Run IDs are appended to ./run-ids.tsv next to this script so yass-compare
# can be pointed at them afterwards.
#
# Override:
#   N_VALUES="1 2 3"   ENGINES="edfs"   SOAK_SECONDS=600   ./run-scaling.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCALING_DIR="$(cd "$HERE/.." && pwd)"
RUN_IDS_FILE="$HERE/run-ids.tsv"

N_VALUES="${N_VALUES:-1 2 3 5 8}"
ENGINES="${ENGINES:-tus edfs}"
SOAK_SECONDS="${SOAK_SECONDS:-25200}"            # 6h soak + 1h drain
READY_TIMEOUT="${READY_TIMEOUT:-600s}"
DELETE_TIMEOUT="${DELETE_TIMEOUT:-300s}"

[ -f "$RUN_IDS_FILE" ] || printf "n\tengine\tnamespace\trun_id\tstarted_at\n" > "$RUN_IDS_FILE"

wait_namespace_gone() {
  local ns="$1"
  local deadline=$(( $(date +%s) + ${DELETE_TIMEOUT%s} ))
  while kubectl get ns "$ns" >/dev/null 2>&1; do
    if [ "$(date +%s)" -gt "$deadline" ]; then
      echo "  WARN: namespace $ns not deleted within ${DELETE_TIMEOUT}, moving on" >&2
      return 0
    fi
    sleep 5
  done
}

run_variant() {
  local n="$1" engine="$2"
  local nn=$(printf "n%02d" "$n")
  local ns="scaling-${nn}-${engine}"
  local overlay="$SCALING_DIR/${nn}/${engine}"

  echo "=== $nn $engine (namespace=$ns) ==="
  kubectl apply -k "$overlay"

  echo "  waiting for experiment-executor pod (timeout $READY_TIMEOUT) ..."
  kubectl -n "$ns" wait --for=condition=Ready pod \
    -l app=experiment-executor --timeout="$READY_TIMEOUT" || true

  echo "  waiting for metrics-bridge pod (timeout $READY_TIMEOUT) ..."
  kubectl -n "$ns" wait --for=condition=Ready pod \
    -l app=metrics-bridge --timeout="$READY_TIMEOUT" || true

  local started
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  local run_id=""
  for _ in 1 2 3 4 5; do
    run_id="$(kubectl -n "$ns" get pod -l app=metrics-bridge \
      -o jsonpath='{.items[0].metadata.labels.yass-run-id}' 2>/dev/null || true)"
    [ -n "$run_id" ] && break
    sleep 5
  done
  [ -z "$run_id" ] && run_id="unknown"

  printf "%s\t%s\t%s\t%s\t%s\n" "$n" "$engine" "$ns" "$run_id" "$started" >> "$RUN_IDS_FILE"
  echo "  run_id=$run_id  started=$started"

  echo "  soaking for ${SOAK_SECONDS}s ..."
  sleep "$SOAK_SECONDS"

  echo "  tearing down ..."
  kubectl delete -k "$overlay" --wait=false || true
  wait_namespace_gone "$ns"
  echo "  done."
}

for n in $N_VALUES; do
  for engine in $ENGINES; do
    run_variant "$n" "$engine"
  done
done

echo
echo "All runs complete. IDs in $RUN_IDS_FILE"
