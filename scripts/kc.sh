#!/usr/bin/env bash
# kubectl against computa from this machine, as the namespace-scoped "deployer" ServiceAccount.
# Kubeconfig (holds a token, never in the repo): $TICKERTAPE_KUBECONFIG or ~/.kube/tickertape.
# Written by home-lab-infra's scripts/write-kubeconfig.sh tickertape (the project's onboarding, its docs/onboarding.md).
#   scripts/kc.sh get pods
#   scripts/kc.sh get pods -n tickertape
set -euo pipefail
cfg="${TICKERTAPE_KUBECONFIG:-$HOME/.kube/tickertape}"
[ -f "$cfg" ] || { echo "missing $cfg: write it with scripts/write-kubeconfig.sh tickertape in home-lab-infra (its docs/onboarding.md)" >&2; exit 1; }
exec kubectl --kubeconfig "$cfg" "$@"
