#!/usr/bin/env bash
# Copy a built image to computa and import it into k3s: scripts/ship.sh <service> [tag]
# `sudo k3s ctr images import` needs a password on computa, so the import step needs a TTY.
# Run it from a real terminal window (Git Bash or PowerShell). Claude Code's `!` prefix has no TTY, so sudo fails there.
set -euo pipefail
svc=${1:?usage: ship.sh <service> [tag]}
tag=${2:-0.1.0}
img="tickertape/$svc:$tag"
tar="${TMPDIR:-/tmp}/tickertape-$svc-$tag.tar"
remote="/tmp/tickertape-$svc-$tag.tar"

docker save "$img" -o "$tar"
scp "$tar" "computa:$remote"
rm -f "$tar"
ssh -t computa "sudo k3s ctr images import $remote && rm -f $remote && sudo k3s crictl images | grep 'tickertape/$svc'"
