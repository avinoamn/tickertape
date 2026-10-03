#!/usr/bin/env bash
# Helm against computa as the namespace-scoped "deployer" ServiceAccount (same kubeconfig as kc.sh), release namespace tickertape.
# Helm keeps its release state in Secrets inside the namespace, which the deployer may manage.
#   scripts/helm.sh list
#   scripts/helm.sh template tickertape charts/tickertape      (use repo-relative paths: helm.exe is a native Windows binary)
set -euo pipefail
cfg="${TICKERTAPE_KUBECONFIG:-$HOME/.kube/tickertape}"
[ -f "$cfg" ] || { echo "missing $cfg: run scripts/bootstrap-access.sh" >&2; exit 1; }
exec helm --kubeconfig "$cfg" -n tickertape "$@"
