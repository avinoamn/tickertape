#!/usr/bin/env bash
# Deploy steps 1-3 (Postgres, schema, poller, ner, laya + ingress) to computa's k3s. CHANGES CLUSTER STATE: ask first.
#   SEC_USER_AGENT="<name> <email>" scripts/deploy.sh
# Prereqs: scripts/bootstrap-access.sh was run once, and the poller image is imported on computa (scripts/ship.sh poller).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
kc="$root/scripts/kc.sh"
ns=tickertape

# Namespace + RBAC are an admin step (scripts/bootstrap-access.sh); the deployer cannot create namespaces.
"$root/scripts/create-secrets.sh"

"$kc" apply -f - < "$root/k8s/postgres.yaml"
"$kc" rollout status statefulset/postgres -n $ns --timeout=180s

# Schema ConfigMap from the file, then re-run the (idempotent) init Job.
# (relative path from the repo root: kubectl.exe is a native Windows binary and cannot read /dev/stdin)
(cd "$root" && "$kc" create configmap db-schema -n $ns --from-file=schema.sql=db/schema.sql --dry-run=client -o yaml) \
  | "$kc" apply -f -
"$kc" delete job db-init -n $ns --ignore-not-found
"$kc" apply -f - < "$root/k8s/db-init-job.yaml"
"$kc" wait --for=condition=complete job/db-init -n $ns --timeout=120s

"$kc" apply -f - < "$root/k8s/poller-cronjob.yaml"

# ner: first start downloads the model (~1 GB) into the PVC, so allow a long rollout.
"$kc" apply -f - < "$root/k8s/ner.yaml"
# laya: same, ~0.8 GB model download on first start (images: scripts/ship.sh ner, scripts/ship.sh laya).
"$kc" apply -f - < "$root/k8s/laya.yaml"
"$kc" apply -f - < "$root/k8s/ingress.yaml"
"$kc" rollout status deployment/ner -n $ns --timeout=600s
"$kc" rollout status deployment/laya -n $ns --timeout=900s
echo "deployed. trigger a run now: scripts/kc.sh create job poller-manual-\$(date +%s) --from=cronjob/poller -n $ns"
