#!/usr/bin/env bash
# Publish grafana/dashboards/*.json as ConfigMaps labelled grafana_dashboard=1 in namespace `monitoring`; the Grafana
# sidecar picks them up within a minute. CHANGES CLUSTER STATE (admin kubectl): ask first. Safe to re-run.
#   scripts/apply-dashboards.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
kc="$root/scripts/kc.sh"
kca="$root/scripts/kc-admin.sh"
cd "$root"   # kubectl.exe is a native Windows binary: use repo-relative paths, never /dev/stdin

for f in grafana/dashboards/*.json; do
  name="$(basename "$f" .json)"
  "$kc" create configmap "dashboard-$name" -n monitoring --from-file="$name.json=$f" --dry-run=client -o yaml \
    | "$kc" label --local -f - grafana_dashboard=1 -o yaml \
    | "$kca" apply -f -
done
