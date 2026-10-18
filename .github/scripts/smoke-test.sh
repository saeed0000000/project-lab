#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-default}"
BACKEND_LOCAL_PORT="${BACKEND_LOCAL_PORT:-13000}"
FRONTEND_LOCAL_PORT="${FRONTEND_LOCAL_PORT:-18080}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"

TMP_DIR="$(mktemp -d)"
BACKEND_PF_PID=""
FRONTEND_PF_PID=""

cleanup() {
  for pid in "$BACKEND_PF_PID" "$FRONTEND_PF_PID"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done

  rm -rf "$TMP_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for cmd in kubectl curl grep; do
  command -v "$cmd" >/dev/null 2>&1 ||
    fail "Required command not found: $cmd"
done

echo "==> Checking Kubernetes services"
kubectl -n "$NAMESPACE" get svc backend frontend >/dev/null

echo "==> Waiting for Backend rollout"
kubectl -n "$NAMESPACE" rollout status \
  deployment/backend --timeout="$ROLLOUT_TIMEOUT"

echo "==> Waiting for Frontend rollout"
kubectl -n "$NAMESPACE" rollout status \
  deployment/frontend --timeout="$ROLLOUT_TIMEOUT"

start_forward() {
  local service="$1"
  local remote_port="$2"
  local local_port="$3"
  local probe_path="$4"
  local log_file="$5"
  local pid_var="$6"
  local url="http://127.0.0.1:${local_port}${probe_path}"

  # Reuse an existing working port-forward when available.
  if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
    echo "Reusing existing endpoint: $url"
    return 0
  fi

  echo "==> Starting port-forward for $service"
  kubectl -n "$NAMESPACE" port-forward \
    --address 127.0.0.1 \
    "svc/$service" "${local_port}:${remote_port}" \
    >"$log_file" 2>&1 &

  local pid=$!
  local -n pid_ref="$pid_var"
  pid_ref="$pid"

  for ((attempt = 1; attempt <= 30; attempt++)); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      echo "Port-forward ready: $url"
      return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Port-forward failed for $service:" >&2
      cat "$log_file" >&2
      return 1
    fi

    sleep 1
  done

  echo "Timed out waiting for $service port-forward:" >&2
  cat "$log_file" >&2
  return 1
}

start_forward backend 3000 "$BACKEND_LOCAL_PORT" \
  /health "$TMP_DIR/backend.log" BACKEND_PF_PID

start_forward frontend 80 "$FRONTEND_LOCAL_PORT" \
  / "$TMP_DIR/frontend.log" FRONTEND_PF_PID

echo "==> Smoke test: Backend /health"
health_body="$(
  curl -fsS --max-time 10 \
    "http://127.0.0.1:${BACKEND_LOCAL_PORT}/health"
)" || fail "Backend /health did not return a successful HTTP response"

grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' \
  <<<"$health_body" ||
  fail "Backend /health returned an unexpected response: $health_body"

echo "PASS: Backend /health"

echo "==> Smoke test: Backend /ready"
ready_body="$(
  curl -fsS --max-time 10 \
    "http://127.0.0.1:${BACKEND_LOCAL_PORT}/ready"
)" || fail "Backend /ready did not return a successful HTTP response"

grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' \
  <<<"$ready_body" ||
  fail "Backend is not reporting ready: $ready_body"

grep -Eq '"database"[[:space:]]*:[[:space:]]*"ok"' \
  <<<"$ready_body" ||
  fail "Backend readiness response does not confirm database status: $ready_body"

echo "PASS: Backend /ready and database status"

echo "==> Smoke test: Frontend /"
frontend_status="$(
  curl -sS --max-time 10 \
    -o "$TMP_DIR/frontend.html" \
    -w '%{http_code}' \
    "http://127.0.0.1:${FRONTEND_LOCAL_PORT}/"
)" || fail "Frontend request failed"

[[ "$frontend_status" == "200" ]] ||
  fail "Frontend returned HTTP $frontend_status instead of HTTP 200"

[[ -s "$TMP_DIR/frontend.html" ]] ||
  fail "Frontend returned an empty response body"

echo "PASS: Frontend HTTP 200 with non-empty response"

echo
echo "All smoke tests passed."
