#!/usr/bin/env bash
# Step 4, part 2 (CHANGES CLUSTER STATE: ask first). Installs/upgrades kube-prometheus-stack in namespace `monitoring`
# with Helm on computa (over SSH, admin kubeconfig), then the dashboards.
# Prereq: scripts/create-monitoring-secrets.sh. Heavy for a 4-core Celeron: run when laya's backlog is drained.
#   scripts/install-monitoring.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
chart_version=91.9.0

# Values go over stdin. --repo avoids a `helm repo add` on the server. --wait: returns once everything is ready.
ssh computa "export KUBECONFIG=~/.kube/config; helm upgrade --install kps kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version $chart_version \
  -n monitoring --create-namespace -f - --wait --timeout 10m" < "$root/k8s/monitoring/values.yaml"

# The ServiceMonitors for ner and laya belong to the tickertape chart. They are only rendered when the CRDs exist, so if the
# chart was installed before this stack, run scripts/deploy.sh again to create them.

"$root/scripts/apply-dashboards.sh"
echo "installed. Grafana: http://computa:30004 (user admin; password: see scripts/create-monitoring-secrets.sh output)"
