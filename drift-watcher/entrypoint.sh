#!/bin/sh
set -eu

: "${GH_TOKEN:?GH_TOKEN must be supplied through the Kubernetes Secret}"
: "${KUBERNETES_SERVICE_HOST:?This container must run inside Kubernetes}"

export GH_TOKEN
export DRIFT_REPO_DIR="${DRIFT_REPO_DIR:-/work/project-lab}"

umask 077

mkdir -p "$HOME/.kube" "$HOME/.config/gh" /work

test -r /var/run/secrets/kubernetes.io/serviceaccount/token
test -r /var/run/secrets/kubernetes.io/serviceaccount/ca.crt

cat > "$HOME/.kube/config" <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
  - name: in-cluster
    cluster:
      server: https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT_HTTPS:-443}
      certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
contexts:
  - name: watcher
    context:
      cluster: in-cluster
      namespace: default
      user: drift-watcher
current-context: watcher
users:
  - name: drift-watcher
    user:
      tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
KUBECONFIG

chmod 0600 "$HOME/.kube/config"
export KUBECONFIG="$HOME/.kube/config"

gh auth setup-git

REPO_URL="https://github.com/saeed0000000/project-lab.git"

if [ -d "$DRIFT_REPO_DIR/.git" ]; then
    git -C "$DRIFT_REPO_DIR" fetch --quiet origin main
    git -C "$DRIFT_REPO_DIR" checkout --force --detach origin/main
else
    if [ -e "$DRIFT_REPO_DIR" ]; then
        rmdir "$DRIFT_REPO_DIR" || {
            echo "ERROR: Workspace exists but is not an empty directory or Git clone." >&2
            exit 1
        }
    fi

    git clone --depth 1 --branch main "$REPO_URL" "$DRIFT_REPO_DIR"
fi

exec python3 /opt/drift-watcher/drift-pr-watcher.py
