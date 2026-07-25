#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="/run/safeexec-health"
LOCK_FILE="${STATE_DIR}/watchdog.lock"
FAILURE_THRESHOLD="${SAFEEXEC_HEALTH_FAILURE_THRESHOLD:-2}"

mkdir -p "${STATE_DIR}"
exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

check_service() {
  local service="$1"
  local endpoint="$2"
  local failure_file="${STATE_DIR}/${service}.failures"

  if systemctl is-active --quiet "${service}" \
    && curl -fsS --connect-timeout 1 --max-time 2 "${endpoint}" >/dev/null; then
    rm -f "${failure_file}"
    return
  fi

  local failures=0
  if [[ -f "${failure_file}" ]]; then
    read -r failures <"${failure_file}" || failures=0
  fi
  failures=$((failures + 1))
  printf '%s\n' "${failures}" >"${failure_file}"
  logger -t safeexec-watchdog \
    "${service} health failure ${failures}/${FAILURE_THRESHOLD}"

  if (( failures < FAILURE_THRESHOLD )); then
    return
  fi

  logger -t safeexec-watchdog \
    "restarting ${service} after ${failures} consecutive health failures"
  systemctl restart "${service}"
  rm -f "${failure_file}"
}

# Check dependencies before consumers. External Guard/JOY readiness is
# deliberately excluded: an external outage must fail closed, not restart X5.
check_service safeexec-runtime.service http://127.0.0.1:8790/healthz
check_service safeexec-orchestrator.service http://127.0.0.1:8789/healthz
check_service safeexec-dashboard.service http://127.0.0.1:8787/healthz
