#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${SAFEEXEC_ROOT}/.run/edge-dashboard/dashboard.pid"

if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}"
  fi
  rm -f "${PID_FILE}"
  echo "stopped edge dashboard"
fi
