#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
RUN_DIR="${SAFEEXEC_ROOT}/.run/x5"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
PRIVATE_KEY="${SAFEEXEC_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/private_key.txt}"
WORK_ORDER_PUBLIC_KEY="${SAFEEXEC_WORK_ORDER_PUBLIC_KEY:-${SAFEEXEC_ROOT}/.run/work_order_public_key.txt}"
MISSION="${SAFEEXEC_MISSION:-${SAFEEXEC_ROOT}/config/mission_x5.yaml}"
ENV_FILE="${SAFEEXEC_X5_ENV_FILE:-${SAFEEXEC_ROOT}/.run/x5-agent.env}"
GUARD_URL="${SAFEEXEC_GUARD_URL:-http://30.201.220.34:8788}"
RUNTIME_USER="${SAFEEXEC_RUNTIME_USER:-safeexec-runtime}"
AGENT_USER="${SAFEEXEC_AGENT_USER:-safeexec-agent}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

for required in "${PYTHON_BIN}" "${PRIVATE_KEY}" "${WORK_ORDER_PUBLIC_KEY}" "${MISSION}"; do
  if [[ ! -e "${required}" ]]; then
    echo "Missing required X5 deployment file: ${required}" >&2
    exit 1
  fi
done
for service_user in "${RUNTIME_USER}" "${AGENT_USER}"; do
  if ! id "${service_user}" >/dev/null 2>&1; then
    echo "Missing service user ${service_user}; run scripts/install_x5_edge.sh" >&2
    exit 1
  fi
done
if [[ "${SAFEEXEC_AGENT_PROVIDER:-replay}" == "openai" && -z "${LLM_API_KEY:-}" ]]; then
  echo "LLM_API_KEY is required for the openai Agent provider" >&2
  exit 1
fi
if [[ "${SAFEEXEC_ENABLE_UNSAFE_DEMO:-0}" == "1" && -z "${LAB_LEGACY_TOKEN:-}" ]]; then
  echo "LAB_LEGACY_TOKEN is required for the unsafe baseline" >&2
  exit 1
fi

mkdir -p "${RUN_DIR}"

if systemctl cat safeexec-runtime.service >/dev/null 2>&1; then
  systemctl restart safeexec-runtime.service
  systemctl restart safeexec-orchestrator.service
  for endpoint in \
    "http://127.0.0.1:8790/healthz" \
    "http://127.0.0.1:8789/healthz"; do
    ready=0
    for _ in {1..40}; do
      if curl -fsS --max-time 1 "${endpoint}" >/dev/null 2>&1; then
        ready=1
        break
      fi
      sleep 0.25
    done
    if [[ "${ready}" != "1" ]]; then
      echo "Service failed readiness at ${endpoint}" >&2
      systemctl --no-pager --full status safeexec-runtime safeexec-orchestrator
      exit 1
    fi
  done
  echo "SafeExec X5 edge controller is managed by systemd"
  curl -sS --max-time 3 http://127.0.0.1:8789/v1/preflight || true
  echo
  exit 0
fi

start_service() {
  local name="$1"
  shift
  local pid_file="${RUN_DIR}/${name}.pid"
  local log_file="${RUN_DIR}/${name}.log"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    echo "${name} already running (PID $(cat "${pid_file}"))"
    return
  fi
  rm -f "${pid_file}"
  nohup setsid "$@" >"${log_file}" 2>&1 </dev/null &
  echo $! >"${pid_file}"
  echo "started ${name} (PID $(cat "${pid_file}"), log ${log_file})"
}

start_service runtime \
  runuser -u "${RUNTIME_USER}" -- \
  env -u LLM_API_KEY -u LAB_LEGACY_TOKEN \
  "${SAFEEXEC_ROOT}/scripts/run_x5_runtime.sh"

start_service orchestrator \
  runuser --preserve-environment -u "${AGENT_USER}" -- \
  "${SAFEEXEC_ROOT}/scripts/run_x5_orchestrator.sh"

for endpoint in \
  "http://127.0.0.1:8790/healthz" \
  "http://127.0.0.1:8789/healthz"; do
  for _ in {1..30}; do
    if curl -fsS --max-time 1 "${endpoint}" >/dev/null; then
      break
    fi
    sleep 0.2
  done
  curl -fsS --max-time 2 "${endpoint}" >/dev/null
done

echo "SafeExec X5 edge controller is ready"
echo "Runtime:      http://192.168.128.10:8790"
echo "Orchestrator: http://192.168.128.10:8789"
echo "Guard:        ${GUARD_URL}"
