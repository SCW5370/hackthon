#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this script as root on the RDK X5" >&2
  exit 1
fi

DEVICE_HOSTNAME="${SAFEEXEC_X5_HOSTNAME:-safeexec-x5}"
if [[ ! "${DEVICE_HOSTNAME}" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]; then
  echo "Invalid SAFEEXEC_X5_HOSTNAME: ${DEVICE_HOSTNAME}" >&2
  exit 1
fi

hostnamectl set-hostname "${DEVICE_HOSTNAME}"
if systemctl cat avahi-daemon.service >/dev/null 2>&1; then
  systemctl enable avahi-daemon.service
  systemctl restart avahi-daemon.service
fi

echo "SafeExec edge identity configured: ${DEVICE_HOSTNAME}.local"
