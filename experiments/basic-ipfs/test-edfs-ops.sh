#!/usr/bin/env bash

set -eEuo pipefail

prog=$(basename -- $0)

function log () {
    # log LEVEL MESSAGE
    local level="$1"; shift 1
    echo "$(date -Is) ${prog} $$ ${level} $@"
}

function fail () {
    # fail CODE MESSAGE
    local code="$1"; shift 1
    log ERROR "$@"
    exit $code
}

main () {
    seed_node=ipfs-0

    log INFO starting

    log INFO "getting a list of all ready ipfs pods"
    all_pods=$(kubectl get pods -o jsonpath='{range .items[*]}{.status.containerStatuses[*].ready.true}{.metadata.name}{ "\n"}{end}')
    log INFO "all pods: $all_pods"
    echo "$all_pods" | grep -q "$seed_node" || fail 1 "missing seed node [$seed_node] in the list of pods [$all_pods]"

    log INFO "running ipfs id on $seed_node"
    seed_node_ipfs_id=$(kubectl exec $seed_node -- ipfs id)
    [ "$seed_node_ipfs_id" ] || fail 2 "missing ipfs id from the seed node"

    log INFO "getting ID and address of $seed_node"
    seed_node_id=$(echo "$seed_node_ipfs_id" | jq -r '.ID')
    seed_node_address=$(echo "$seed_node_ipfs_id" | jq -r '.Addresses[0]')
    [ "$seed_node_id" ] || fail 3 "missing ID of the seed node"
    [ "$seed_node_address" ] || fail 4 "missing address of the seed node"

    # connect nodes to the swarm by providing them with bootstrap addresses
    for pod in $all_pods; do
        log INFO "adding seed node address [$seed_node_address] to $pod bootstrap list"
        kubectl exec $pod -- ipfs bootstrap add $seed_node_address
        sleep 1
    done

    # We see 15-25s delay, TODO: check if it's configurable

    test_020_content_propagation ipfs-1 ipfs-0

    for pod in $all_pods; do
        log INFO "checking ipfs swarm peers on $pod"
        kubectl exec $pod -- ipfs swarm peers
    done

    log INFO finished
}


test_020_content_propagation () {
    # test_020_content_propagation SRC_POD TGT_POD
    src_pod=$1
    tgt_pod=$2

    log INFO "adding semi-random content on $src_pod"
    treasure=$(mktemp -u "Golden Fleece XXX")
    treasure_cid=$(kubectl exec $src_pod -- sh -c "echo $treasure | ipfs add --quieter")
    log INFO "added CID on $src_pod: $treasure_cid"

    log INFO "retrieving CID $treasure_cid on $tgt_pod"
    retrieval_start=$(date +%s)
    retrieval_timeout=90
    retrieval_file=$(mktemp)
    retrieval_ok=""
    while true; do
        if kubectl exec $tgt_pod -- timeout 5 ipfs cat $treasure_cid > $retrieval_file 2>/dev/null; then
            retrieval_ok=ok
        fi
        elapsed=$(( $(date +%s) - retrieval_start ))
        if [ "$retrieval_ok" = "ok" ]; then
            break
        fi
        if [ $elapsed -ge $retrieval_timeout ]; then
            fail 5 "retrieval of CID $treasure_cid timed out after $elapsed seconds"
        fi
    done
    # Final retrieve (should not time out)
    retrieved=$(cat $retrieval_file)
    log INFO "test content retrieved after $elapsed seconds: $retrieved"
    if ! [ "$retrieved" = "$treasure" ]; then
        fail 6 "content verification failed (stored: $treasure, retrieved: $retrieved)"
    fi
}

main "$@"

