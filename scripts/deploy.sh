#!/usr/bin/env bash
# Install or upgrade the tickertape chart in namespace tickertape (Postgres, schema, poller, ner, laya, ingress,
# ServiceMonitors). CHANGES CLUSTER STATE: ask first.
#   SEC_USER_AGENT="<name> <email>" scripts/deploy.sh [extra helm args]
#   scripts/deploy.sh --set laya.image.tag=0.1.2        (SEC_USER_AGENT is only needed while poller-config does not exist yet)
# Prereqs: scripts/bootstrap-access.sh was run once, and the images are on the node (scripts/ship.sh <svc>).
# NOT for adopting an install that was made with plain kubectl apply: a failed first install with --atomic is uninstalled,
# which deletes the adopted objects. Use the commands in docs/operations.md ("Adopting an install") for that one run.
# --atomic waits for everything to be ready (ner/laya download their models on first start, hence the long timeout)
# and rolls the release back if the upgrade fails.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"   # helm.exe is a native Windows binary: pass repo-relative paths

# Secrets are not part of the chart. postgres-credentials is created once, poller-config is re-applied from SEC_USER_AGENT.
if [ -n "${SEC_USER_AGENT:-}" ] || ! scripts/kc.sh get secret poller-config >/dev/null 2>&1; then
  scripts/create-secrets.sh
else
  echo "poller-config exists and SEC_USER_AGENT is not set: leaving the Secrets alone"
fi

scripts/helm.sh upgrade --install tickertape charts/tickertape --atomic --timeout 20m "$@"
scripts/helm.sh status tickertape
