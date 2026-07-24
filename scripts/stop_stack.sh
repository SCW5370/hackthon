#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SAFEEXEC_ROOT}/.run/v2"

for name in dashboard orchestrator runtime; do
  pid_file="${RUN_DIR}/${name}.pid"
  if [[ ! -f "${pid_file}" ]]; then
    continue
  fi
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}"
    wait "${pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
  echo "stopped ${name}"
done
