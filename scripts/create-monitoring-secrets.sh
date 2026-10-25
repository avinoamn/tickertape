#!/usr/bin/env bash
# Step 4, part 1 (CHANGES CLUSTER STATE: ask first). Creates namespace `monitoring`, the Grafana admin Secret and the
# Alertmanager webhook Secret. Values never touch the repo, argv or logs. Needs admin access (scripts/kc-admin.sh).
# Idempotent: existing Secrets are kept, so re-running does not rotate anything.
#   scripts/create-monitoring-secrets.sh
# Nothing here is specific to a project: each project provides its own datasources from its namespace
# (tickertape: scripts/create-grafana-datasource.sh).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
kca="$here/kc-admin.sh"

"$kca" create namespace monitoring --dry-run=client -o yaml | "$kca" apply -f -

if "$kca" get secret grafana-admin -n monitoring >/dev/null 2>&1; then
  echo "grafana-admin exists, leaving it alone"
else
  pw=$(openssl rand -hex 12)
  "$kca" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata: {name: grafana-admin, namespace: monitoring}
stringData:
  admin-user: admin
  admin-password: "$pw"
EOF
fi

# Alertmanager's Discord webhook. Provide it as an env var (Discord: channel settings, Integrations, Webhooks); it is never
# written to the repo, argv or logs. Skipped if the Secret exists (to replace it: delete the Secret, run this again).
if "$kca" get secret alertmanager-discord -n monitoring >/dev/null 2>&1; then
  echo "alertmanager-discord exists, leaving it alone"
elif [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
  "$kca" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata: {name: alertmanager-discord, namespace: monitoring}
stringData:
  webhook-url: "$DISCORD_WEBHOOK_URL"
EOF
else
  echo "DISCORD_WEBHOOK_URL is not set: alertmanager-discord was NOT created, and Alertmanager will not start without it" >&2
  exit 1
fi

echo "done. Grafana login: user admin, password from:"
echo "  scripts/kc-admin.sh get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d"
