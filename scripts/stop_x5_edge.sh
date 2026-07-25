#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
RUN_DIR="${SAFEEXEC_ROOT}/.run/x5"

if systemctl cat safeexec-runtime.service >/dev/null 2>&1; then
  systemctl stop safeexec-dashboard.service safeexec-orchestrator.service safeexec-runtime.service
  echo "stopped X5 systemd services"
  exit 0
fi

for name in orchestrator runtime; do
  pid_file="${RUN_DIR}/${name}.pid"
  if [[ ! -f "${pid_file}" ]]; then
    continue
  fi
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
  echo "stopped ${name}"
done
