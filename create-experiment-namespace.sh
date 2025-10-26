#!/bin/bash
NS=$1

if [ "$NS" == "" ]; then
  echo "Argument 1 should be namespace name" >&2
  exit 1
fi

kubectl create namespace "${NS}" || true
kubectl -n "${NS}" apply -f docker-sectet.yaml && \

