#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${SAFEEXEC_ROOT}/.mock-server.pid"
LOG_FILE="${SAFEEXEC_ROOT}/.mock-server.log"

if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "SafeExec mock server is already running with PID $(cat "${PID_FILE}")"
  exit 0
fi

SAFEEXEC_HOST="${SAFEEXEC_HOST:-0.0.0.0}"
python3 "${SAFEEXEC_ROOT}/dev/mock_server.py" \
  --host "${SAFEEXEC_HOST}" >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
echo "SafeExec mock dashboard started: http://127.0.0.1:8787"
echo "RDK Fact endpoint: http://192.168.128.20:8787/api/facts"
echo "Log: ${LOG_FILE}"
