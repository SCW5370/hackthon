#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SAFEEXEC_ROOT}/.run/v2"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
PRIVATE_KEY="${SAFEEXEC_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/private_key.txt}"
GUARD_URL="${SAFEEXEC_GUARD_URL:-http://127.0.0.1:8788}"
FACT_MODE="${SAFEEXEC_FACT_MODE:-demo}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -f "${PRIVATE_KEY}" ]]; then
  echo "Runtime private key not found: ${PRIVATE_KEY}" >&2
  exit 1
fi

mkdir -p "${RUN_DIR}"

start_service() {
  local name="$1"
  shift
  local pid_file="${RUN_DIR}/${name}.pid"
  local log_file="${RUN_DIR}/${name}.log"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    echo "${name} already running (PID $(cat "${pid_file}"))"
    return
  fi
  nohup "$@" >"${log_file}" 2>&1 </dev/null &
  echo $! >"${pid_file}"
  echo "started ${name} (PID $(cat "${pid_file}"), log ${log_file})"
}

start_service runtime \
  "${PYTHON_BIN}" -m runtime.runtime_http \
  --host 127.0.0.1 \
  --port 8790 \
  --mission "${SAFEEXEC_ROOT}/config/mission.yaml" \
  --key "${PRIVATE_KEY}" \
  --guard-url "${GUARD_URL}/v1/execute"

start_service orchestrator \
  "${PYTHON_BIN}" -m orchestrator.orchestrator_http \
  --host 127.0.0.1 \
  --port 8789 \
  --runtime-url http://127.0.0.1:8790 \
  --fact-mode "${FACT_MODE}" \
  --recovery-delay 1.5 \
  --enable-testing

start_service dashboard \
  "${PYTHON_BIN}" -m dev.dashboard_server \
  --host 127.0.0.1 \
  --port 8787 \
  --orchestrator-url http://127.0.0.1:8789 \
  --runtime-url http://127.0.0.1:8790 \
  --guard-url "${GUARD_URL}"

echo "Dashboard:    http://127.0.0.1:8787"
echo "Orchestrator: http://127.0.0.1:8789"
echo "Runtime:      http://127.0.0.1:8790"
echo "Guard:        ${GUARD_URL}"
