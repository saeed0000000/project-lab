#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KUBE_CONTEXT="$(kubectl config current-context)"

echo "Kubernetes context: ${KUBE_CONTEXT}"

if ! kubectl --context "${KUBE_CONTEXT}" \
  get namespace argocd >/dev/null 2>&1; then
  echo "Error: namespace argocd not found." >&2
  echo "Install Argo CD before running this script." >&2
  exit 1
fi

kubectl --context "${KUBE_CONTEXT}" \
  apply -f "${SCRIPT_DIR}/root-app.yaml"
