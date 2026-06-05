#!/usr/bin/env bash
# UC5 — General Failure (EDFS only). Drives the tiered parameter sweep
# declared in tiers.yaml: for every entry, render the templates, apply,
# wait for terminal state, export artefacts, delete the namespace.
#
# 20% of sats (min 1) are producers: each writes 5 x 32M files, spaced
# 2m apart (CHECK_INTERVAL_SECONDS=120, MAX_PHOTOS=5).
# Every fsNode (producers, relays, GS) carries a recurring non-Destroy
# fault schedule: rotate NetworkBandwidthReduced / NetworkFailure /
# DiskFull / DiskFailure; intervalMean 5m, jitter 50%, duration ~30s.
# GS are receive-only without END_ON_ANY (log all files until run ends).

set -euo pipefail

usage() {
  cat <<USAGE
usage: $0 --tier <1|2|3|all> [--kubeconfig <path>] [--dry-run]

  --tier         Which tier from tiers.yaml to run. Default: 1.
  --kubeconfig   Path to the kubeconfig to use. Defaults to
                 \$KUBECONFIG if exactly one file; otherwise
                 ~/.kube/config.
  --dry-run      Render YAML to _runs/<run_id>/ and print the matrix;
                 do NOT apply.

Outputs:
  _runs/<run_id>/{ns,expdef,exp}.yaml — rendered manifests per run
  _runs/<run_id>/run.log              — per-run kubectl output
  _runs/<run_id>.tar.gz               — bundle from yass-export
USAGE
  exit 2
}

TIER=1
KCFG_ARG=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --tier)       TIER=$2; shift 2 ;;
    --kubeconfig) KCFG_ARG=$2; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage ;;
    *)            echo "unknown arg: $1" >&2; usage ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
EXPERIMENTS_ROOT=$(cd "$HERE/../.." && pwd)
EXPORT_BIN=${YASS_EXPORT_BIN:-"$EXPERIMENTS_ROOT/tools/yass-export/yass-export.sh"}

if [[ -n $KCFG_ARG ]]; then
  KCFG=$KCFG_ARG
elif [[ -n ${KUBECONFIG-} && $KUBECONFIG != *:* && -f $KUBECONFIG ]]; then
  KCFG=$KUBECONFIG
else
  KCFG="$HOME/.kube/config"
fi
echo "using kubeconfig: $KCFG"
[[ -f $KCFG ]] || { echo "kubeconfig not found at $KCFG" >&2; exit 4; }
export KUBECONFIG=$KCFG

# EDFS images and bootstrap config (reused from UC1/big-scale).
edfs_engine_img=ghcr.io/duobitx/yass-edfs-engine
edfs_node_img=ghcr.io/duobitx/yass-edfs-engine-node
edfs_proxy_img=ghcr.io/duobitx/yass-edfs-engine-proxy
edfs_cluster_secret=50896c846aed59faeec45d1779e6b9ca6fac89d135d988b52c2f366f1b7f373d
edfs_swarm_key=e0e6161ec71e8e8c6d18c64dd8ee37f178fc50cadf58f20d08c481131a09bbae
edfs_bootstrap_peer=estrack-new-norcia
edfs_bootstrap_peer_id=12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
edfs_bootstrap_peer_key="CAESQNU9ILPST19ucrp2ZzY4BN1+LnoLK5XnH86F8m2Ce3R7m7n8DOzBtVlE/3GS9UCxChfcU+Y/lkjR/T5nBJEujGY="

# Fault type rotation (no Destroy).
FAULT_TYPES=(NetworkBandwidthReduced NetworkFailure DiskFull DiskFailure)

# UC5 producer parameters.
FILES_PER_PRODUCER=5
FILE_SIZE=32M
FILE_PRIORITY=default
CHECK_INTERVAL_SECONDS=120   # 2m between photos

# Known ESTRACK ground station names (must match Layout fsNode names).
GROUND_STATIONS=(
  estrack-new-norcia
  estrack-kiruna
  estrack-redu
  estrack-cebreros
  estrack-santa-maria
  estrack-kourou
  estrack-malargue
)

# ---------------------------------------------------------------------------
# Behaviour generators
# ---------------------------------------------------------------------------

# Fault block for a fsNode at position <idx> (0-based).
# Rotates through FAULT_TYPES; seed = 1000 + idx.
fault_block() {
  local idx=$1
  local name=$2
  local fault=${FAULT_TYPES[$((idx % ${#FAULT_TYPES[@]}))]}
  local seed=$((1000 + idx))
  local out
  out=$(cat <<-YAML
      hardwareEvents:
        - name: random-${fault,,}
          type: ${fault}
          startOffset: 1m
          schedule:
            intervalMean: 5m
            intervalJitterPercent: 50
            durationMean: 30s
            durationJitterPercent: 50
            seed: ${seed}
YAML
)
  if [[ $fault == NetworkBandwidthReduced ]]; then
    out+=$(cat <<-YAML

          params:
            networkBandwidth:
              reductionPercent: 75
YAML
)
  fi
  printf '%s' "$out"
}

# Producer behaviour: periodic agent writing FILES_PER_PRODUCER files.
producer_behaviour() {
  local idx=$1
  local name=$2
  cat <<-YAML
    - fsNode: ${name}
      agent:
        image: ghcr.io/duobitx/yass-agent-periodic
        envsMap:
          FILE_SIZE: "${FILE_SIZE}"
          FILE_PRIORITY: "${FILE_PRIORITY}"
          MAX_PHOTOS: "${FILES_PER_PRODUCER}"
          CHECK_INTERVAL_SECONDS: "${CHECK_INTERVAL_SECONDS}"
$(fault_block "$idx" "$name")
YAML
}

# Relay behaviour: no agent, only fault schedule.
relay_behaviour() {
  local idx=$1
  local name=$2
  cat <<-YAML
    - fsNode: ${name}
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
        envsMap: {}
$(fault_block "$idx" "$name")
YAML
}

# GS behaviour: receive-only WITHOUT END_ON_ANY (stay alive until run ends).
# GS fault index starts after all sats (so seeds don't collide).
gs_behaviour() {
  local idx=$1
  local name=$2
  cat <<-YAML
    - fsNode: ${name}
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
        envsMap: {}
$(fault_block "$idx" "$name")
YAML
}

# Build the full behaviours block for a given layout file and producer count.
make_behaviours() {
  local layout_file=$1
  local producer_count=$2

  # Extract satellite fsNode names in Layout order.
  mapfile -t sat_names < <(
    grep -E '^\s+- fsNode: (oneweb-)' "$layout_file" \
      | sed 's/.*fsNode: //' | sed 's/[[:space:]]*$//'
  )

  local total_sats=${#sat_names[@]}
  local idx=0
  for name in "${sat_names[@]}"; do
    if (( idx < producer_count )); then
      producer_behaviour "$idx" "$name"
    else
      relay_behaviour "$idx" "$name"
    fi
    (( idx++ )) || true
  done

  # GS fault seeds start at total_sats so they never overlap sat seeds.
  local gs_idx=0
  for gs in "${GROUND_STATIONS[@]}"; do
    gs_behaviour "$(( total_sats + gs_idx ))" "$gs"
    (( gs_idx++ )) || true
  done
}

# ---------------------------------------------------------------------------
# Tier reader
# ---------------------------------------------------------------------------
read_tier() {
  python3 - "$HERE/tiers.yaml" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for e in data.get(sys.argv[2], []):
    print("|".join(str(e.get(k, "")) for k in ("engine", "sat_count", "rf")))
PY
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
tiers_to_run=()
if [[ $TIER == all ]]; then
  tiers_to_run=(tier_1 tier_2 tier_3)
else
  tiers_to_run=("tier_$TIER")
fi

if [[ $DRY_RUN -eq 0 ]]; then
  for f in "$HERE/_layouts"/n*.yaml; do
    kubectl apply -f "$f" >/dev/null
  done
  echo "applied $(ls "$HERE/_layouts"/n*.yaml | wc -l) Layouts"
fi

mkdir -p "$HERE/_runs"

for tier in "${tiers_to_run[@]}"; do
  while IFS='|' read -r engine sat_count rf; do
    [[ -z $engine ]] && continue

    # Producer count: max(1, floor(0.2 * sat_count))
    producer_count=$(python3 -c "import math; print(max(1, math.floor(0.2 * $sat_count)))")

    run_id="uc5-edfs-n$(printf '%02d' "$sat_count")-rf${rf}"
    ns=$run_id
    layout_file=$(printf '%s/_layouts/n%02d.yaml' "$HERE" "$sat_count")
    layout_ref=$(printf 'uc5-n%02d' "$sat_count")

    behaviours=$(make_behaviours "$layout_file" "$producer_count")

    engine_containers=$(cat <<-YAML
  engineContainers:
    - name: edfs-engine
      image: "${edfs_engine_img}"
      env:
        - name: WATCH_DIR
          value: "/mnt/transfer"
        - name: EDFS_CLUSTER_SECRET
          value: ${edfs_cluster_secret}
        - name: EDFS_CLUSTER_HOST
          value: "/ip4/127.0.0.1/tcp/9094"
        - name: EDFS_CONNECTION_RETRIES
          value: "3"
        - name: EDFS_REPLICATION_FACTOR
          value: "${rf}"
    - name: edfs-engine-node
      image: "${edfs_node_img}"
      readinessProbe:
        exec:
          command: ["sh", "-c", "nc -z 127.0.0.1 5001"]
        initialDelaySeconds: 5
        periodSeconds: 5
        timeoutSeconds: 2
      env:
        - name: EDFS_SWARM_KEY
          value: ${edfs_swarm_key}
        - name: EDFS_BOOTSTRAP_PEER_NAME
          value: ${edfs_bootstrap_peer}
        - name: EDFS_BOOTSTRAP_PEER_HOSTNAME
          value: ${edfs_bootstrap_peer}
        - name: EDFS_BOOTSTRAP_PEER_ID
          value: ${edfs_bootstrap_peer_id}
        - name: EDFS_BOOTSTRAP_PEER_KEY
          value: ${edfs_bootstrap_peer_key}
    - name: edfs-engine-proxy
      image: "${edfs_proxy_img}"
      readinessProbe:
        exec:
          command: ["sh", "-c", "nc -z 127.0.0.1 9094"]
        initialDelaySeconds: 5
        periodSeconds: 5
        timeoutSeconds: 2
      env:
        - name: EDFS_CLUSTER_SECRET
          value: ${edfs_cluster_secret}
        - name: EDFS_BOOTSTRAP_PEER_NAME
          value: ${edfs_bootstrap_peer}
        - name: EDFS_BOOTSTRAP_PEER_HOSTNAME
          value: ${edfs_bootstrap_peer}
        - name: EDFS_BOOTSTRAP_PEER_ID
          value: ${edfs_bootstrap_peer_id}
        - name: EDFS_BOOTSTRAP_PEER_KEY
          value: ${edfs_bootstrap_peer_key}
YAML
)

    out="$HERE/_runs/${run_id}"
    mkdir -p "$out"

    total_files=$(( producer_count * FILES_PER_PRODUCER ))
    echo "[$run_id] producers=${producer_count}/${sat_count}  files_in_flight=${total_files}  rf=${rf}"

    export RUN_ID=$run_id NAMESPACE=$ns
    export MAX_DURATION=8h
    export LAYOUT_REF=$layout_ref
    export BEHAVIOURS=$behaviours
    export ENGINE_CONTAINERS=$engine_containers

    envsubst < "$HERE/_template/00_namespace.yaml.tmpl"             > "$out/00_namespace.yaml"
    envsubst < "$HERE/_template/02_experiment_definition.yaml.tmpl" > "$out/02_experiment_definition.yaml"
    envsubst < "$HERE/_template/03_experiment.yaml.tmpl"            > "$out/03_experiment.yaml"

    # kustomize overlay for this variant: kubectl apply -k _runs/<run_id>/
    cat > "$out/kustomization.yaml" <<KUST
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - 00_namespace.yaml
  - 02_experiment_definition.yaml
  - 03_experiment.yaml
KUST

    echo "[$run_id] rendered → $out"
    [[ $DRY_RUN -eq 1 ]] && continue

    kubectl apply -f "$out/00_namespace.yaml" \
                  -f "$out/02_experiment_definition.yaml" \
                  -f "$out/03_experiment.yaml" >> "$out/run.log" 2>&1

    echo "[$run_id] applied; waiting for terminal state…"
    until state=$(kubectl -n "$ns" get experiment "$run_id" -o jsonpath='{.status.experimentState}' 2>/dev/null) \
       && [[ $state =~ ^(Ongoing|Errored|TimedOut|Success|Failure)$ ]]; do
      sleep 5
    done
    if [[ $state == Ongoing ]]; then
      until state=$(kubectl -n "$ns" get experiment "$run_id" -o jsonpath='{.status.experimentState}' 2>/dev/null) \
         && [[ $state =~ ^(Errored|TimedOut|Success|Failure)$ ]]; do
        sleep 15
      done
    fi
    echo "[$run_id] terminal state: $state"

    if [[ -x $EXPORT_BIN ]]; then
      "$EXPORT_BIN" "$ns" --out "$HERE/_runs" >> "$out/run.log" 2>&1 \
        || echo "[$run_id] yass-export failed (see $out/run.log)" >&2
    else
      echo "[$run_id] yass-export binary not found at $EXPORT_BIN; skipping bundle"
    fi

    kubectl delete ns "$ns" --wait=false >> "$out/run.log" 2>&1 || true

    sleep 30
  done < <(read_tier "$tier")
done

# Global kustomization: `kubectl apply -k _runs/` launches every rendered
# variant at once (each subdir is its own overlay).
{
  echo "apiVersion: kustomize.config.k8s.io/v1beta1"
  echo "kind: Kustomization"
  echo "resources:"
  for d in "$HERE/_runs"/*/; do
    [ -f "${d}kustomization.yaml" ] && echo "  - $(basename "$d")"
  done
} > "$HERE/_runs/kustomization.yaml"
echo "wrote global kustomization → $HERE/_runs/kustomization.yaml"

echo "done."
