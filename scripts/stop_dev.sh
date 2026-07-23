#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${SAFEEXEC_ROOT}/.mock-server.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "SafeExec mock server is not running"
  exit 0
fi

PID="$(cat "${PID_FILE}")"
if kill -0 "${PID}" 2>/dev/null; then
  kill "${PID}"
  wait "${PID}" 2>/dev/null || true
fi
rm -f "${PID_FILE}"
echo "SafeExec mock server stopped"

