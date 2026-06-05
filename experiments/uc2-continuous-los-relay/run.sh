#!/usr/bin/env bash
# UC2 — Continuous LOS Relay. Drives the tiered parameter sweep
# declared in tiers.yaml: for every entry, render the templates, apply,
# wait for terminal state, export artefacts, delete the namespace.
#
# Differences from UC1:
#   - Every SAT is a producer (periodic agent, MAX_PHOTOS=1, FILE_SIZE=32M).
#   - Every SAT and every GS carries a recurring hardwareEvents fault schedule
#     (NetworkBandwidthReduced, NetworkFailure, DiskFull, DiskFailure) with a
#     per-fsNode seed derived from fnv64(run_id + fsNode name) for
#     reproducibility.  No Destroy fault type.
#   - GS agent is yass-agent-receive-only WITHOUT END_ON_ANY — it stays alive
#     logging every RECEIVED event until the experiment ends.
#
# See README.md for the experiment description.

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

# Repo paths
HERE=$(cd "$(dirname "$0")" && pwd)
EXPERIMENTS_ROOT=$(cd "$HERE/../.." && pwd)
EXPORT_BIN=${YASS_EXPORT_BIN:-"$EXPERIMENTS_ROOT/tools/yass-export/yass-export.sh"}

# Kubeconfig resolution: explicit --kubeconfig wins, otherwise honour
# $KUBECONFIG if it is a single file, otherwise fall back to
# ~/.kube/config.
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

# Engine images — identical to UC1 (same EDFS cluster keys / bootstrap peer).
tus_img=ghcr.io/duobitx/yass-tus-fs-engine:latest
edfs_engine_img=ghcr.io/duobitx/yass-edfs-engine
edfs_node_img=ghcr.io/duobitx/yass-edfs-engine-node
edfs_proxy_img=ghcr.io/duobitx/yass-edfs-engine-proxy
edfs_cluster_secret=50896c846aed59faeec45d1779e6b9ca6fac89d135d988b52c2f366f1b7f373d
edfs_swarm_key=e0e6161ec71e8e8c6d18c64dd8ee37f178fc50cadf58f20d08c481131a09bbae
edfs_bootstrap_peer=estrack-new-norcia
edfs_bootstrap_peer_id=12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
edfs_bootstrap_peer_key="CAESQNU9ILPST19ucrp2ZzY4BN1+LnoLK5XnH86F8m2Ce3R7m7n8DOzBtVlE/3GS9UCxChfcU+Y/lkjR/T5nBJEujGY="

# FNV-32 seed derivation: maps (run_id + fsNode) to a small integer seed
# reproducibly so every re-run of the same tier entry uses the same fault
# schedule.  Pure bash, no external tools required.
fnv32_seed() {
  local s="${1}${2}"
  local h=2166136261
  local i
  for (( i=0; i<${#s}; i++ )); do
    printf -v c '%d' "'${s:$i:1}"
    h=$(( (h * 16777619) ^ c ))
    h=$(( h & 0x7FFFFFFF ))  # keep positive (bash int is 64-bit signed)
  done
  # Map to range 1000..99999 so seeds are human-readable but never 0.
  echo $(( 1000 + (h % 98999) ))
}

# Fault types rotated per fsNode index (matching big-scale gen.py recipe).
FAULT_TYPES=(NetworkBandwidthReduced NetworkFailure DiskFull DiskFailure)

# make_behaviours <layout_file> <priority> <run_id>
# Emits one Behaviour block per fsNode:
#   - satellites: periodic-agent producer (MAX_PHOTOS=1) + fault schedule
#   - ground stations: receive-only (no END_ON_ANY) + fault schedule
make_behaviours() {
  local layout_file=$1
  local priority=$2
  local run_id=$3

  local idx=0
  local fsn="" ntype="" fault seed

  while IFS= read -r line; do
    if [[ $line =~ ^[[:space:]]*-[[:space:]]fsNode:[[:space:]]*(.*) ]]; then
      fsn="${BASH_REMATCH[1]}"
      ntype=""
    fi
    if [[ $line =~ nodeType:[[:space:]]*(.*) ]]; then
      ntype="${BASH_REMATCH[1]}"
    fi
    if [[ -n ${fsn:-} && -n ${ntype:-} ]]; then
      fault=${FAULT_TYPES[$((idx % ${#FAULT_TYPES[@]}))]}
      seed=$(fnv32_seed "$run_id" "$fsn")

      if [[ $ntype == satellite ]]; then
        # Stagger CHECK_INTERVAL_SECONDS so phases drift over a few iterations.
        local interval=$(( 270 + (idx * 7) % 60 ))
        cat <<-YAML
    - fsNode: ${fsn}
      agent:
        image: ghcr.io/duobitx/yass-agent-periodic
        envsMap:
          MAX_PHOTOS: "1"
          FILE_SIZE: "32M"
          FILE_PRIORITY: "${priority}"
          CHECK_INTERVAL_SECONDS: "${interval}"
      hardwareEvents:
        - name: fault-${fault,,}
          type: ${fault}
          startOffset: 1m
          schedule:
            intervalMean: 5m
            intervalJitterPercent: 50
            durationMean: 30s
            durationJitterPercent: 50
            seed: ${seed}
YAML
        if [[ $fault == NetworkBandwidthReduced ]]; then
          cat <<-YAML
          params:
            networkBandwidth:
              capBitsPerSec: 100000
YAML
        fi
      else
        # Ground station: receive-only, no END_ON_ANY, fault schedule still applied.
        cat <<-YAML
    - fsNode: ${fsn}
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
      hardwareEvents:
        - name: fault-${fault,,}
          type: ${fault}
          startOffset: 1m
          schedule:
            intervalMean: 5m
            intervalJitterPercent: 50
            durationMean: 30s
            durationJitterPercent: 50
            seed: ${seed}
YAML
        if [[ $fault == NetworkBandwidthReduced ]]; then
          cat <<-YAML
          params:
            networkBandwidth:
              capBitsPerSec: 100000
YAML
        fi
      fi
      idx=$(( idx + 1 ))
      fsn=""
      ntype=""
    fi
  done < "$layout_file"
}

# Pull the tier entries via python. One line per entry:
# engine|sat_count|file_size|priority|rf
read_tier() {
  python3 - "$HERE/tiers.yaml" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for e in data.get(sys.argv[2], []):
    print("|".join(str(e.get(k, "")) for k in ("engine", "sat_count", "file_size", "priority", "rf")))
PY
}

# Tier list to run.
tiers_to_run=()
if [[ $TIER == all ]]; then
  tiers_to_run=(tier_1 tier_2 tier_3)
else
  tiers_to_run=("tier_$TIER")
fi

# Apply Layouts first (cluster-scoped; cheap to re-apply).
if [[ $DRY_RUN -eq 0 ]]; then
  for f in "$HERE/_layouts"/n*.yaml; do
    kubectl apply -f "$f" >/dev/null
  done
  echo "applied $(ls "$HERE/_layouts"/n*.yaml | wc -l) Layouts"
fi

# Render-and-run loop.
mkdir -p "$HERE/_runs"
for tier in "${tiers_to_run[@]}"; do
  while IFS='|' read -r engine sat_count file_size priority rf; do
    [[ -z $engine ]] && continue
    priority=${priority:-default}

    # RunId convention from README:
    #   EDFS: uc2-edfs-p<priority>-n<NN>-rf<rf>
    #   TUS:  uc2-tus-n<NN>
    if [[ $engine == edfs ]]; then
      run_id="uc2-edfs-p${priority,,}-n$(printf '%02d' "$sat_count")-rf${rf}"
    else
      run_id="uc2-tus-n$(printf '%02d' "$sat_count")"
    fi
    ns=$run_id
    layout_file=$(printf '%s/_layouts/n%02d.yaml' "$HERE" "$sat_count")
    layout_ref=$(printf 'uc2-n%02d' "$sat_count")

    behaviours=$(make_behaviours "$layout_file" "$priority" "$run_id")

    case $engine in
      tus)
        engine_containers=$(cat <<-YAML
  engineContainers:
    - name: engine-tus
      image: "${tus_img}"
      imagePullPolicy: Always
      env:
        - name: GROUND_STATIONS
          value: "*"
YAML
)
        ;;
      edfs)
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
        ;;
    esac

    max_duration=6h
    out="$HERE/_runs/${run_id}"
    mkdir -p "$out"

    # Render templates. BEHAVIOURS and ENGINE_CONTAINERS are multi-line strings
    # passed via envsubst. The template uses literal ${VAR} so they substitute
    # cleanly without shell expansion inside the YAML.
    export RUN_ID=$run_id NAMESPACE=$ns
    export FILE_SIZE=$file_size PRIORITY=$priority MAX_DURATION=$max_duration
    export LAYOUT_REF=$layout_ref
    export ENGINE_CONTAINERS=$engine_containers
    export BEHAVIOURS=$behaviours
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

    # Apply
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

    # Bundle
    if [[ -x $EXPORT_BIN ]]; then
      "$EXPORT_BIN" "$ns" --out "$HERE/_runs" >> "$out/run.log" 2>&1 \
        || echo "[$run_id] yass-export failed (see $out/run.log)" >&2
    else
      echo "[$run_id] yass-export binary not found at $EXPORT_BIN; skipping bundle"
    fi

    # Tear down
    kubectl delete ns "$ns" --wait=false >> "$out/run.log" 2>&1 || true

    # Pause for MQTT broker drain before the next aggregator publishes _meta_.
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
