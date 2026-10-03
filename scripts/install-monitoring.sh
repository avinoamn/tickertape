#!/usr/bin/env bash
# Step 4, part 2 (CHANGES CLUSTER STATE: ask first). Installs/upgrades kube-prometheus-stack in namespace `monitoring`
# with Helm on computa (over SSH, admin kubeconfig), then the ner/laya ServiceMonitors and the dashboards.
# Prereq: scripts/create-monitoring-secrets.sh. Heavy for a 4-core Celeron: run when laya's backlog is drained.
#   scripts/install-monitoring.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
chart_version=91.9.0

# Values go over stdin. --repo avoids a `helm repo add` on the server. --wait: returns once everything is ready.
ssh computa "export KUBECONFIG=~/.kube/config; helm upgrade --install kps kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version $chart_version \
  -n monitoring --create-namespace -f - --wait --timeout 10m" < "$root/k8s/monitoring/values.yaml"

# The CRDs exist now, and the deployer may manage ServiceMonitors in tickertape (k8s/rbac.yaml).
"$root/scripts/kc.sh" apply -f - < "$root/k8s/servicemonitors.yaml"

"$root/scripts/apply-dashboards.sh"
echo "installed. Grafana: http://computa:30004 (user admin; password: see scripts/create-monitoring-secrets.sh output)"
