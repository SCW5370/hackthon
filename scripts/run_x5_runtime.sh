#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
PRIVATE_KEY="${SAFEEXEC_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/private_key.txt}"
WORK_ORDER_PUBLIC_KEY="${SAFEEXEC_WORK_ORDER_PUBLIC_KEY:-${SAFEEXEC_ROOT}/.run/work_order_public_key.txt}"
MISSION="${SAFEEXEC_MISSION:-${SAFEEXEC_ROOT}/config/mission_x5.yaml}"
GUARD_CANDIDATES="${SAFEEXEC_GUARD_CANDIDATES:-http://30.201.220.34:8788,http://100.123.243.7:8788}"

IFS=',' read -r -a candidate_values <<<"${GUARD_CANDIDATES}"
runtime_args=(
  "${PYTHON_BIN}" -m runtime.runtime_http
  --host 0.0.0.0
  --port 8790
  --mission "${MISSION}"
  --key "${PRIVATE_KEY}"
  --work-order-public-key "${WORK_ORDER_PUBLIC_KEY}"
)
for candidate in "${candidate_values[@]}"; do
  if [[ -n "${candidate}" ]]; then
    runtime_args+=(--guard-candidate "${candidate}")
  fi
done
if [[ "${#candidate_values[@]}" -gt 0 ]]; then
  runtime_args+=(--guard-url "${candidate_values[0]%/}/v1/execute")
fi

exec "${runtime_args[@]}"
