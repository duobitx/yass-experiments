#!/usr/bin/env bash
# UC1 — Rapid Disaster Response. Drives the tiered parameter sweep
# declared in tiers.yaml: for every entry, render the templates, apply,
# wait for terminal state, export artefacts, delete the namespace.
#
# See README.md for the experiment description and the yass-uc-
# experiment-implement skill for the pattern this driver follows.

set -euo pipefail

usage() {
  cat <<USAGE
usage: $0 --tier <1|2|3|all> [--cluster <kind|cf|prod>] [--dry-run]

  --tier      Which tier from tiers.yaml to run. Default: 1.
  --cluster   kind | cf | prod. Default: cf. n55 entries refuse cf.
  --dry-run   Render YAML to _runs/<run_id>/ and print the matrix;
              do NOT apply.

Outputs:
  _runs/<run_id>/{ns,expdef,exp}.yaml — rendered manifests per run
  _runs/<run_id>/run.log              — per-run kubectl output
  _runs/<run_id>.tar.gz               — bundle from yass-export
USAGE
  exit 2
}

TIER=1
CLUSTER=cf
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --tier)    TIER=$2; shift 2 ;;
    --cluster) CLUSTER=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *)         echo "unknown arg: $1" >&2; usage ;;
  esac
done

# Repo paths
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../../.." && pwd)              # /home/gruszecm/esa
EXPERIMENTS_ROOT=$(cd "$HERE/../.." && pwd)          # …/yass-experiments
EXPORT_BIN=${YASS_EXPORT_BIN:-"$EXPERIMENTS_ROOT/tools/yass-export/yass-export.sh"}

# Kubeconfig resolution — always derived from --cluster, never from a
# pre-existing $KUBECONFIG path-list (which gets messy in dev shells).
case $CLUSTER in
  kind) KCFG="$HOME/.kube/config" ;;
  cf)   KCFG="$REPO_ROOT/cf-kubeconfig.yaml" ;;
  prod) KCFG="$REPO_ROOT/Decentralized-Storage_config.yaml"
        echo "prod cluster requested — type 'yes prod' to confirm:" >&2
        read -r line; [[ $line == "yes prod" ]] || { echo "aborted." >&2; exit 3; } ;;
  *)    echo "unknown cluster: $CLUSTER" >&2; exit 2 ;;
esac
echo "using kubeconfig: $KCFG"
[[ -f $KCFG ]] || { echo "kubeconfig not found at $KCFG" >&2; exit 4; }
export KUBECONFIG=$KCFG

# Engine image map
edfs_img=ghcr.io/duobitx/yass-edfs-fs-engine:latest
tus_img=ghcr.io/duobitx/yass-tus-fs-engine:latest

# Producer is the first satellite in the Layout — same TLE across every
# Layout file so the headline curve is comparable.
PRODUCER=oneweb-0012

# Read the requested tier list as JSON via yq.
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

# A producing run (END_ON_ANY) terminates as soon as the first GS gets
# the file. Every other behaviour is a receiver: agent ghcr.io/
# duobitx/yass-agent-receive-only with END_ON_ANY=true.
make_extra_behaviours() {
  local layout_file=$1
  awk '
    /  - fsNode:/ { fsnode=$3; next }
    fsnode && /  - fsNode:|^---|^[^ ]/ { fsnode="" }
    END { }
    # emit a Behaviour for every non-producer fsNode
    fsnode && fsnode != "'"$PRODUCER"'" { print fsnode }
  ' "$layout_file" \
    | grep -v "^$" \
    | sort -u \
    | while read -r fsn; do
        cat <<-YAML
    - fsNode: $fsn
      agent:
        image: ghcr.io/duobitx/yass-agent-receive-only
        envsMap:
          END_ON_ANY: "true"
YAML
      done
}

# Pull the tier entries via python (yq from snap is unusable in our
# sandbox). One line per entry: engine|sat_count|file_size|priority|rf
read_tier() {
  python3 - "$HERE/tiers.yaml" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for e in data.get(sys.argv[2], []):
    print("|".join(str(e.get(k, "")) for k in ("engine", "sat_count", "file_size", "priority", "rf")))
PY
}

# Render-and-run loop.
mkdir -p "$HERE/_runs"
for tier in "${tiers_to_run[@]}"; do
  while IFS='|' read -r engine sat_count file_size priority rf; do
    [[ -z $engine ]] && continue
    priority=${priority:-default}

    # runId per the README's convention
    if [[ $engine == edfs ]]; then
      run_id="uc1-edfs-S${file_size}-P${priority}-N$(printf '%02d' "$sat_count")-RF${rf}"
    else
      run_id="uc1-tus-S${file_size}-N$(printf '%02d' "$sat_count")"
    fi
    # Namespace = run_id lowercased. K8s namespaces must match
    # [a-z0-9-]+; our run_id contains 'P' / 'N' / 'RF' uppercase
    # letters so we just downcase the whole thing.
    ns=${run_id,,}
    layout_file=$(printf '%s/_layouts/n%02d.yaml' "$HERE" "$sat_count")

    # Cluster-fit guard
    if [[ $sat_count == 55 && $CLUSTER == cf ]]; then
      echo "[$run_id] refusing n55 on cf (CPU bottleneck — use --cluster prod). skip." >&2
      continue
    fi

    case $engine in
      edfs) engine_image=$edfs_img;
            engine_extra_env=$(cat <<-YAML
        - name: EDFS_REPLICATION_FACTOR
          value: "${rf}"
YAML
            ) ;;
      tus)  engine_image=$tus_img; engine_extra_env="" ;;
    esac

    # Default maxDuration from README
    max_duration=2h
    layout_ref=$(printf 'uc1-n%02d' "$sat_count")
    extra=$(make_extra_behaviours "$layout_file")

    out="$HERE/_runs/${run_id}"
    mkdir -p "$out"

    # Render
    export RUN_ID=$run_id NAMESPACE=$ns ENGINE=$engine ENGINE_IMAGE=$engine_image
    export FILE_SIZE=$file_size PRIORITY=$priority MAX_DURATION=$max_duration
    export PRODUCER LAYOUT_REF=$layout_ref ENGINE_EXTRA_ENV=$engine_extra_env
    export EXTRA_BEHAVIOURS=$extra
    envsubst < "$HERE/_template/00_namespace.yaml.tmpl"          > "$out/00_namespace.yaml"
    envsubst < "$HERE/_template/02_experiment_definition.yaml.tmpl" > "$out/02_experiment_definition.yaml"
    envsubst < "$HERE/_template/03_experiment.yaml.tmpl"         > "$out/03_experiment.yaml"

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

echo "done."
