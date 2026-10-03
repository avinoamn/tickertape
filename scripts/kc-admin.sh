#!/usr/bin/env bash
# ADMIN kubectl: runs on computa over SSH with the cluster-admin kubeconfig. Use only for cluster-scoped
# work (namespace, RBAC, Helm/CRDs) and bootstrapping. Day-to-day work uses scripts/kc.sh (deployer, no SSH).
#   scripts/kc-admin.sh get nodes
set -euo pipefail
args=$(printf '%q ' "$@")
exec ssh computa "KUBECONFIG=~/.kube/config kubectl $args"
