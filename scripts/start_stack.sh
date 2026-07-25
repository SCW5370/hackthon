#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SAFEEXEC_ROOT}/.run/v2"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
PRIVATE_KEY="${SAFEEXEC_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/private_key.txt}"
WORK_ORDER_PRIVATE_KEY="${SAFEEXEC_WORK_ORDER_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/work_order_private_key.txt}"
WORK_ORDER_PUBLIC_KEY="${SAFEEXEC_WORK_ORDER_PUBLIC_KEY:-${SAFEEXEC_ROOT}/.run/work_order_public_key.txt}"
GUARD_URL="${SAFEEXEC_GUARD_URL:-http://127.0.0.1:8788}"
FACT_MODE="${SAFEEXEC_FACT_MODE:-demo}"
AGENT_PROVIDER="${SAFEEXEC_AGENT_PROVIDER:-replay}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.qnaigc.com/v1}"
LLM_MODEL="${LLM_MODEL:-deepseek/deepseek-v4-pro-202606}"
LEGACY_URL="${LAB_LEGACY_URL:-http://127.0.0.1:8791}"
UNSAFE_DEMO="${SAFEEXEC_ENABLE_UNSAFE_DEMO:-0}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -f "${PRIVATE_KEY}" ]]; then
  echo "Runtime private key not found: ${PRIVATE_KEY}" >&2
  exit 1
fi
if [[ ! -f "${WORK_ORDER_PRIVATE_KEY}" && ! -f "${WORK_ORDER_PUBLIC_KEY}" ]]; then
  "${PYTHON_BIN}" "${SAFEEXEC_ROOT}/scripts/gen_keys.py" \
    --private-key "${WORK_ORDER_PRIVATE_KEY}" \
    --public-key "${WORK_ORDER_PUBLIC_KEY}"
elif [[ ! -f "${WORK_ORDER_PRIVATE_KEY}" || ! -f "${WORK_ORDER_PUBLIC_KEY}" ]]; then
  echo "WorkOrder signing keypair is incomplete in ${SAFEEXEC_ROOT}/.run" >&2
  exit 1
fi
if [[ "${AGENT_PROVIDER}" == "openai" && -z "${LLM_API_KEY:-}" ]]; then
  echo "LLM_API_KEY is required when SAFEEXEC_AGENT_PROVIDER=openai" >&2
  exit 1
fi
if [[ "${UNSAFE_DEMO}" == "1" && -z "${LAB_LEGACY_TOKEN:-}" ]]; then
  echo "LAB_LEGACY_TOKEN is required when unsafe baseline is enabled" >&2
  exit 1
fi

mkdir -p "${RUN_DIR}"
export LLM_BASE_URL LLM_MODEL

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
  --work-order-public-key "${WORK_ORDER_PUBLIC_KEY}" \
  --guard-url "${GUARD_URL}/v1/execute"

ORCHESTRATOR_ARGS=(
  "${PYTHON_BIN}" -m orchestrator.orchestrator_http
  --host 127.0.0.1 \
  --port 8789 \
  --runtime-url http://127.0.0.1:8790 \
  --legacy-url "${LEGACY_URL}" \
  --fact-mode "${FACT_MODE}" \
  --agent-provider "${AGENT_PROVIDER}" \
  --require-trusted-work-order \
  --recovery-delay 1.5 \
  --enable-testing
)
if [[ "${UNSAFE_DEMO}" == "1" ]]; then
  ORCHESTRATOR_ARGS+=(--enable-unsafe-demo)
fi
start_service orchestrator "${ORCHESTRATOR_ARGS[@]}"

start_service dashboard \
  "${PYTHON_BIN}" -m dev.dashboard_server \
  --host 127.0.0.1 \
  --port 8787 \
  --orchestrator-url http://127.0.0.1:8789 \
  --runtime-url http://127.0.0.1:8790 \
  --work-order-key "${WORK_ORDER_PRIVATE_KEY}" \
  --guard-url "${GUARD_URL}"

echo "Dashboard:    http://127.0.0.1:8787"
echo "Configuration:http://127.0.0.1:8787/config"
echo "Orchestrator: http://127.0.0.1:8789"
echo "Runtime:      http://127.0.0.1:8790"
echo "Guard:        ${GUARD_URL}"
