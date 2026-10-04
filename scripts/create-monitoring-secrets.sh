#!/usr/bin/env bash
# Step 4, part 1 (CHANGES CLUSTER STATE: ask first). Creates namespace `monitoring`, the Grafana admin Secret, the
# read-only Postgres role `grafana_ro` and Secret `grafana-db` (its password). Values never touch the repo, argv or logs.
# Idempotent: existing Secrets are kept, so re-running does not rotate anything.
#   scripts/create-monitoring-secrets.sh
# Needs admin access (scripts/kc-admin.sh) for the namespace/Secrets and the deployer (scripts/kc.sh) for the DB role.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
kc="$here/kc.sh"
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

if "$kca" get secret grafana-db -n monitoring >/dev/null 2>&1; then
  pw=$("$kca" get secret grafana-db -n monitoring -o jsonpath='{.data.GRAFANA_PG_PASSWORD}' | base64 -d)
  echo "grafana-db exists, reusing its password for the role"
else
  pw=$(openssl rand -hex 16)
  "$kca" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata: {name: grafana-db, namespace: monitoring}
stringData:
  GRAFANA_PG_PASSWORD: "$pw"
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

# Read-only role: SELECT on the pipeline tables only, writes impossible, runaway queries cut after 15 s.
# The SQL goes over stdin so the password is not visible in any process list. Hex passwords need no quoting.
"$kc" exec -i -n tickertape postgres-0 -- psql -U tickertape -d tickertape -v ON_ERROR_STOP=1 -q <<EOF
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN CREATE ROLE grafana_ro LOGIN; END IF;
END \$\$;
ALTER ROLE grafana_ro PASSWORD '$pw';
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '15s';
GRANT CONNECT ON DATABASE tickertape TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON items, decisions, feed_state TO grafana_ro;
EOF

echo "done. Grafana login: user admin, password from:"
echo "  scripts/kc-admin.sh get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d"
