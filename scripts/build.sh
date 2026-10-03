#!/usr/bin/env bash
# Build a service image from the repo root: scripts/build.sh <service> [tag]
# The tag must match the one in charts/tickertape/values.yaml (no mutable tags: k3s uses IfNotPresent).
set -euo pipefail
svc=${1:?usage: build.sh <service> [tag]}
tag=${2:-0.1.0}
cd "$(dirname "$0")/.."
docker build -f "services/$svc/Dockerfile" -t "tickertape/$svc:$tag" .
echo "built tickertape/$svc:$tag"
