#!/usr/bin/env bash
# UC4 — Sat Failure (pole). Drives the tiered parameter sweep declared in
# tiers.yaml: for every entry, render the templates, apply, wait for terminal
# state, export artefacts, delete the namespace.
#
# Run-id format:
#   EDFS: uc4-edfs-p<priority>-td<T_destroy>-n<NN>-rf3
#   TUS:  uc4-tus-td<T_destroy>-n<NN>   (TUS ignores priority + rf)
#
# Producer is the synthetic satellite `producer` (see tools/make-producer-
# layouts.py): a polar orbit phased so its sub-point is over the SOUTH pole at
# the TLE epoch, which equals simulationStartTime. Every ESTRACK station is
# out of LOS at t=0 by construction, so the "out of LOS at photo time"
# precondition holds without tuning per sat_count.

set -euo pipefail

usage() {
  cat <<USAGE
usage: $0 --tier <1|2|3|all> [--kubeconfig <path>] [--dry-run]

  --tier         Which tier from tiers.yaml to run. Default: 1.
  --kubeconfig   Path to kubeconfig. Defaults to \$KUBECONFIG if a single
                 file; otherwise ~/.kube/config.
  --dry-run      Render YAML to _runs/<run_id>/ and print the matrix;
                 do NOT apply.

Outputs per run:
  _runs/<run_id>/{ns,expdef,exp}.yaml — rendered manifests
  _runs/<run_id>/run.log              — kubectl output
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

# Engine images — same as UC1 (all UCs share the same image set).
tus_img=ghcr.io/duobitx/yass-tus-fs-engine:latest
edfs_engine_img=ghcr.io/duobitx/yass-edfs-engine
edfs_node_img=ghcr.io/duobitx/yass-edfs-engine-node
edfs_proxy_img=ghcr.io/duobitx/yass-edfs-engine-proxy
edfs_cluster_secret=50896c846aed59faeec45d1779e6b9ca6fac89d135d988b52c2f366f1b7f373d
edfs_swarm_key=e0e6161ec71e8e8c6d18c64dd8ee37f178fc50cadf58f20d08c481131a09bbae
edfs_bootstrap_peer=estrack-new-norcia
edfs_bootstrap_peer_id=12D3KooWLJFr7nesNtEQrQ9PqyjT8kvxbdUqfF4xwZZKEzJHnEhT
edfs_bootstrap_peer_key="CAESQNU9ILPST19ucrp2ZzY4BN1+LnoLK5XnH86F8m2Ce3R7m7n8DOzBtVlE/3GS9UCxChfcU+Y/lkjR/T5nBJEujGY="

# The producer is always the first plane-diverse pick from the OneWeb roster.
# It is the only node that produces a file; all other SATs are pure relays.
PRODUCER=producer

# UC4 fixed parameters. (priority is now an EDFS sweep variable — see tiers.yaml.)
FILE_SIZE=32M
RF=3
# Fallback priority for tier rows that omit it (TUS rows — TUS ignores priority).
DEFAULT_PRIORITY=high
# Engine-specific run budget (maxDuration). EDFS needs a long window: a peer that
# received a replica before the producer was destroyed may take a long time to
# fly into LOS with a GS. TUS has no inter-satellite relay, so once the sole
# copy's producer is destroyed the file is unrecoverable — there is no point
# running the full EDFS budget. We stop the TUS run TUS_GRACE after the Destroy
# event: maxDuration_TUS = T_destroy + TUS_GRACE.
EDFS_MAX_DURATION=4h
TUS_GRACE_SECONDS=600   # 10 minutes after the disaster (Destroy) event

tiers_to_run=()
if [[ $TIER == all ]]; then
  tiers_to_run=(tier_1 tier_2 tier_3)
else
  tiers_to_run=("tier_$TIER")
fi

# Apply cluster-scoped Layouts (idempotent).
if [[ $DRY_RUN -eq 0 ]]; then
  for f in "$HERE/_layouts"/n*.yaml; do
    kubectl apply -f "$f" >/dev/null
  done
  echo "applied $(ls "$HERE/_layouts"/n*.yaml | wc -l) Layouts"
fi

# Build receive-only behaviours for every non-producer fsNode, branching on node
# type: ground stations gate on first delivery (END_ON_ANY → reached-a-GA metric);
# relay satellites report success immediately and keep relaying (they never
# receive the file, and their no-LOS `tc` filter cuts the END_ON_ANY signal, so
# gating them on receipt would hang the experiment forever).
make_extra_behaviours() {
  local layout_file=$1
  awk -v producer="$PRODUCER" '
    /^  - fsNode:/ {
      if (fsnode != "" && fsnode != producer) print fsnode "\t" type
      fsnode=$3; type="satellite"; next
    }
    /^    nodeType:/ { type=$2 }
    END { if (fsnode != "" && fsnode != producer) print fsnode "\t" type }
  ' "$layout_file" \
    | sort -u \
    | while IFS="$(printf '\t')" read -r fsn typ; do
        [ -z "$fsn" ] && continue
        if [ "$typ" = "groundStation" ]; then
          cat <<-YAML
    - fsNode: $fsn
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
        envsMap:
          END_ON_ANY: "true"
YAML
        else
          cat <<-YAML
    - fsNode: $fsn
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
        envsMap:
          REPORT_SUCCESS_ON_START: "true"
YAML
        fi
      done
}

# Parse a tier from tiers.yaml into pipe-delimited lines:
# engine|sat_count|file_size|priority|rf|t_destroy
read_tier() {
  python3 - "$HERE/tiers.yaml" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for e in data.get(sys.argv[2], []):
    print("|".join(str(e.get(k, "")) for k in
          ("engine", "sat_count", "file_size", "priority", "rf", "t_destroy")))
PY
}

# add_duration <go-duration> <seconds> -> Go-duration string.
# Derives the TUS maxDuration as T_destroy + TUS_GRACE. Parses h/m/s components.
add_duration() {
  awk -v d="$1" -v add="$2" 'BEGIN{
    n=""
    for(i=1;i<=length(d);i++){c=substr(d,i,1)
      if(c ~ /[0-9]/){n=n c}
      else if(c=="h"){s+=n*3600;n=""} else if(c=="m"){s+=n*60;n=""} else if(c=="s"){s+=n;n=""}
    }
    s+=add; h=int(s/3600); s-=h*3600; m=int(s/60); s-=m*60
    out=(h?h"h":"")(m?m"m":"")(s?s"s":""); print (out==""?"0s":out)
  }'
}

mkdir -p "$HERE/_runs"

for tier in "${tiers_to_run[@]}"; do
  while IFS='|' read -r engine sat_count file_size priority rf t_destroy; do
    [[ -z $engine ]] && continue

    # Strip trailing whitespace / empty fields from optional TUS columns.
    t_destroy=${t_destroy:-15m}
    priority=${priority:-$DEFAULT_PRIORITY}

    # Run-id: uc4-edfs-p<prio>-td<T>-n<NN>-rf3 or uc4-tus-td<T>-n<NN>.
    # t_destroy value is already a Go-duration string (5m / 15m / 45m).
    # priority is an EDFS-only axis (high/default/low); TUS ignores it and omits
    # both the p<prio> and rf tokens (TUS reduces to (sat_count, t_destroy)).
    td_label=${t_destroy//m/m}   # keep as-is; already lowercase
    nn=$(printf '%02d' "$sat_count")
    if [[ $engine == edfs ]]; then
      run_id="uc4-edfs-p${priority,,}-td${td_label}-n${nn}-rf${RF}"
    else
      run_id="uc4-tus-td${td_label}-n${nn}"
    fi
    ns=$run_id
    layout_ref="uc4-n${nn}"
    layout_file="$HERE/_layouts/n${nn}.yaml"

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
        # TUS cannot recover after the producer is destroyed: stop 10m past Destroy.
        max_duration=$(add_duration "$t_destroy" "$TUS_GRACE_SECONDS")
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
          value: "${RF}"
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
        max_duration=$EDFS_MAX_DURATION
        ;;
    esac

    extra=$(make_extra_behaviours "$layout_file")

    out="$HERE/_runs/${run_id}"
    mkdir -p "$out"

    export RUN_ID=$run_id NAMESPACE=$ns
    export FILE_SIZE=$file_size FILE_PRIORITY=$priority
    export MAX_DURATION=$max_duration PRODUCER LAYOUT_REF=$layout_ref
    export T_DESTROY=$t_destroy
    export ENGINE_CONTAINERS=$engine_containers
    export EXTRA_BEHAVIOURS=$extra

    envsubst < "$HERE/_template/00_namespace.yaml.tmpl"               > "$out/00_namespace.yaml"
    envsubst < "$HERE/_template/02_experiment_definition.yaml.tmpl"   > "$out/02_experiment_definition.yaml"
    envsubst < "$HERE/_template/03_experiment.yaml.tmpl"              > "$out/03_experiment.yaml"

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
