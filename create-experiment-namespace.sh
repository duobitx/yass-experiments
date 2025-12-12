#!/bin/bash
NS=$1

if [ "$NS" == "" ]; then
  echo "Argument 1 must be namespace name" >&2
  exit 1
fi

kubectl create namespace "${NS}" && kubectl label namespace "${NS}" yass-namespace=true
echo "Namespace $NS"

