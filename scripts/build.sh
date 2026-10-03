#!/usr/bin/env bash
# Build a service image from the repo root: scripts/build.sh <service> [tag]
# The tag defaults to the contents of services/<service>/VERSION (the version of the code in this tree).
# No mutable tags (k3s uses IfNotPresent). To deploy a local image, point the chart at it (docs/operations.md).
set -euo pipefail
svc=${1:?usage: build.sh <service> [tag]}
cd "$(dirname "$0")/.."
tag=${2:-$(tr -d '[:space:]' < "services/$svc/VERSION")}
docker build -f "services/$svc/Dockerfile" -t "tickertape/$svc:$tag" .
echo "built tickertape/$svc:$tag"
