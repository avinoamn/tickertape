#!/usr/bin/env bash
# Grafana's Postgres datasource for this project (CHANGES CLUSTER STATE: ask first). Runs as the deployer (scripts/kc.sh):
#  - the read-only database role `grafana_ro`, inside postgres-0 (so run it after the first deploy);
#  - Secret `grafana-datasource` in namespace tickertape, labelled grafana_datasource=1, holding a Grafana datasource
#    provisioning file (uid `tickertape-pg`, the uid the dashboards use) with the role's password. The monitoring stack's
#    Grafana sidecar loads it, because it watches this namespace (k8s/monitoring/values.yaml, sidecar.datasources).
# Idempotent: an existing Secret's password is reused, so re-running re-applies the role's settings and rotates nothing.
# To rotate: delete the Secret and run this again. The password never touches the repo, argv or logs.
#   scripts/create-grafana-datasource.sh
set -euo pipefail
kc="$(dirname "$0")/kc.sh"
secret=grafana-datasource
key=tickertape-pg.yaml

if "$kc" get secret "$secret" -n tickertape >/dev/null 2>&1; then
  pw=$("$kc" get secret "$secret" -n tickertape -o jsonpath='{.data.tickertape-pg\.yaml}' | base64 -d \
    | sed -n 's/^ *password: *"\([0-9a-f]*\)" *$/\1/p')
  [ -n "$pw" ] || { echo "$secret exists but holds no password in $key: delete it and run this again" >&2; exit 1; }
  echo "$secret exists, reusing its password"
else
  pw=$(openssl rand -hex 16)
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

# The sidecar writes each key of the Secret as a file into Grafana's datasource provisioning folder and asks Grafana to
# reload, so the key name must be unique across every project's datasources and end in .yaml.
"$kc" apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: $secret
  namespace: tickertape
  labels: {grafana_datasource: "1"}
stringData:
  $key: |
    apiVersion: 1
    datasources:
      - name: tickertape-pg
        uid: tickertape-pg
        type: postgres
        access: proxy
        url: postgres.tickertape.svc.cluster.local:5432
        user: grafana_ro
        database: tickertape
        jsonData:
          database: tickertape
          sslmode: disable
          postgresVersion: 1600
          timescaledb: false
        secureJsonData:
          password: "$pw"
EOF
echo "done. Grafana loads datasource tickertape-pg within a minute; check with: GRAFANA_URL=http://<node>:30004 make verify-dashboards"
