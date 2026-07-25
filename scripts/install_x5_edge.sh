#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this installer as root on the RDK X5" >&2
  exit 1
fi

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
RUNTIME_USER="${SAFEEXEC_RUNTIME_USER:-safeexec-runtime}"
AGENT_USER="${SAFEEXEC_AGENT_USER:-safeexec-agent}"
PRIVATE_KEY="${SAFEEXEC_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/private_key.txt}"
WORK_ORDER_PUBLIC_KEY="${SAFEEXEC_WORK_ORDER_PUBLIC_KEY:-${SAFEEXEC_ROOT}/.run/work_order_public_key.txt}"
ENV_FILE="${SAFEEXEC_X5_ENV_FILE:-${SAFEEXEC_ROOT}/.run/x5-agent.env}"

create_service_user() {
  local user="$1"
  if ! id "${user}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${user}"
    echo "created service user ${user}"
  fi
}

create_service_user "${RUNTIME_USER}"
create_service_user "${AGENT_USER}"

install -d -m 0755 "${SAFEEXEC_ROOT}/.run" "${SAFEEXEC_ROOT}/.run/x5"
for required in "${PRIVATE_KEY}" "${WORK_ORDER_PUBLIC_KEY}" "${ENV_FILE}"; do
  if [[ ! -f "${required}" ]]; then
    echo "Missing deployment secret or key: ${required}" >&2
    exit 1
  fi
done

chown "${RUNTIME_USER}:${RUNTIME_USER}" "${PRIVATE_KEY}"
chmod 0600 "${PRIVATE_KEY}"
chown root:"${RUNTIME_USER}" "${WORK_ORDER_PUBLIC_KEY}"
chmod 0640 "${WORK_ORDER_PUBLIC_KEY}"
chown root:root "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"

echo "X5 execution domains configured"
echo "Agent user:   ${AGENT_USER}"
echo "Runtime user: ${RUNTIME_USER}"
