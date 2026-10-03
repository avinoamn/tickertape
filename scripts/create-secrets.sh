#!/usr/bin/env bash
# Create the cluster Secrets in namespace tickertape. Values never touch the repo or the process list.
#   SEC_USER_AGENT="<name> <email>" scripts/create-secrets.sh
# postgres-credentials is created once and never rotated here (rotating would lock the existing PVC out).
# poller-config is re-applied on every run.
set -euo pipefail
kc="$(dirname "$0")/kc.sh"

if "$kc" get secret postgres-credentials -n tickertape >/dev/null 2>&1; then
  echo "postgres-credentials exists, leaving it alone"
else
  pw=$(openssl rand -hex 16)
  "$kc" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata: {name: postgres-credentials, namespace: tickertape}
stringData:
  POSTGRES_USER: tickertape
  POSTGRES_PASSWORD: "$pw"
  POSTGRES_DB: tickertape
  DATABASE_URL: "postgresql://tickertape:$pw@postgres.tickertape.svc:5432/tickertape"
EOF
fi

: "${SEC_USER_AGENT:?set SEC_USER_AGENT to \"<name> <email>\"}"
"$kc" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata: {name: poller-config, namespace: tickertape}
stringData:
  SEC_USER_AGENT: "$SEC_USER_AGENT"
EOF
