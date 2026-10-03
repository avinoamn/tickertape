#!/usr/bin/env bash
# Build a service image from the repo root: scripts/build.sh <service> [tag]
# The tag defaults to the contents of services/<service>/VERSION (the version of the code in this tree).
# For checking a Dockerfile locally. Released images are built by CI from a release tag (docs/releasing.md).
set -euo pipefail
svc=${1:?usage: build.sh <service> [tag]}
cd "$(dirname "$0")/.."
tag=${2:-$(tr -d '[:space:]' < "services/$svc/VERSION")}
docker build -f "services/$svc/Dockerfile" -t "tickertape/$svc:$tag" .
echo "built tickertape/$svc:$tag"
