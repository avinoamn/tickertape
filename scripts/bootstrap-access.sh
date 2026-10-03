#!/usr/bin/env bash
# One-time (and re-runnable) admin step: create namespace + deployer RBAC on computa, then write a
# kubeconfig for the deployer to ~/.kube/tickertape. CHANGES CLUSTER STATE (namespace tickertape only).
# Server defaults to https://computa:6443 (DNS name is in the API cert); override with API_SERVER.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
admin="$root/scripts/kc-admin.sh"
cfg="${TICKERTAPE_KUBECONFIG:-$HOME/.kube/tickertape}"
server="${API_SERVER:-https://computa:6443}"

"$admin" apply -f - < "$root/k8s/namespace.yaml"
"$admin" apply -f - < "$root/k8s/rbac.yaml"

# The token controller fills the Secret asynchronously.
token=
for _ in $(seq 1 20); do
  token=$("$admin" get secret deployer-token -n tickertape -o jsonpath='{.data.token}')
  [ -n "$token" ] && break
  sleep 1
done
[ -n "$token" ] || { echo "deployer-token was not populated" >&2; exit 1; }
ca=$("$admin" get secret deployer-token -n tickertape -o jsonpath='{.data.ca\.crt}')

mkdir -p "$(dirname "$cfg")"
umask 077
cat > "$cfg" <<KCFG
apiVersion: v1
kind: Config
clusters:
  - name: computa
    cluster: {server: $server, certificate-authority-data: $ca}
users:
  - name: tickertape-deployer
    user: {token: $(printf '%s' "$token" | base64 -d)}
contexts:
  - name: tickertape
    context: {cluster: computa, user: tickertape-deployer, namespace: tickertape}
current-context: tickertape
KCFG
echo "wrote $cfg"
"$root/scripts/kc.sh" get pods
