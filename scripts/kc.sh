#!/usr/bin/env bash
# kubectl against computa from this machine, as the namespace-scoped "deployer" ServiceAccount.
# Kubeconfig (holds a token, never in the repo): $TICKERTAPE_KUBECONFIG or ~/.kube/tickertape.
# Create it with scripts/bootstrap-access.sh.
#   scripts/kc.sh get pods
#   scripts/kc.sh apply -f - < k8s/postgres.yaml
set -euo pipefail
cfg="${TICKERTAPE_KUBECONFIG:-$HOME/.kube/tickertape}"
[ -f "$cfg" ] || { echo "missing $cfg: run scripts/bootstrap-access.sh" >&2; exit 1; }
exec kubectl --kubeconfig "$cfg" "$@"
