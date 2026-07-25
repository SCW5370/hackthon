#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
WORK_ORDER_PRIVATE_KEY="${SAFEEXEC_WORK_ORDER_PRIVATE_KEY:-${SAFEEXEC_ROOT}/.run/work_order_private_key.txt}"
GUARD_URL="${SAFEEXEC_GUARD_URL:-http://30.201.220.34:8788}"

exec "${PYTHON_BIN}" -m dev.dashboard_server \
  --host 0.0.0.0 \
  --port 8787 \
  --orchestrator-url "http://127.0.0.1:8789" \
  --runtime-url "http://127.0.0.1:8790" \
  --guard-url "${GUARD_URL}" \
  --work-order-key "${WORK_ORDER_PRIVATE_KEY}" \
  --work-order-fact-mode "${SAFEEXEC_WORK_ORDER_FACT_MODE:-none}"
