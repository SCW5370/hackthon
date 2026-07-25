#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SAFEEXEC_ROOT}/.run/edge-dashboard"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
WORK_ORDER_PRIVATE_KEY="${SAFEEXEC_WORK_ORDER_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/work_order_private_key.txt}"
X5_HOST="${SAFEEXEC_X5_HOST:-192.168.128.10}"
WINDOWS_HOST="${SAFEEXEC_WINDOWS_HOST:-30.201.220.34}"
PID_FILE="${RUN_DIR}/dashboard.pid"
LOG_FILE="${RUN_DIR}/dashboard.log"

for required in "${PYTHON_BIN}" "${WORK_ORDER_PRIVATE_KEY}"; do
  if [[ ! -e "${required}" ]]; then
    echo "Missing required dashboard file: ${required}" >&2
    exit 1
  fi
done

mkdir -p "${RUN_DIR}"
if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "edge dashboard already running (PID $(cat "${PID_FILE}"))"
  exit 0
fi
rm -f "${PID_FILE}"

nohup "${PYTHON_BIN}" -m dev.dashboard_server \
  --host 127.0.0.1 \
  --port 8787 \
  --orchestrator-url "http://${X5_HOST}:8789" \
  --runtime-url "http://${X5_HOST}:8790" \
  --guard-url "http://${WINDOWS_HOST}:8788" \
  --work-order-key "${WORK_ORDER_PRIVATE_KEY}" \
  --work-order-fact-mode none \
  >"${LOG_FILE}" 2>&1 </dev/null &
echo $! >"${PID_FILE}"

for _ in {1..30}; do
  if curl -fsS --max-time 1 http://127.0.0.1:8787/healthz >/dev/null; then
    echo "SafeExec edge Dashboard is ready at http://127.0.0.1:8787"
    exit 0
  fi
  sleep 0.2
done

echo "Dashboard failed to become healthy; see ${LOG_FILE}" >&2
exit 1
