#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_X5_HOST="${SAFEEXEC_X5_HOST:-192.168.128.10}"
DASHBOARD_URL="${SAFEEXEC_DASHBOARD_URL:-http://127.0.0.1:8787}"

echo "SafeExec demo preflight"
echo
echo "Mac control plane"
curl -fsS --max-time 3 "${DASHBOARD_URL}/healthz"
echo
echo "X5 liveness"
curl -fsS --max-time 3 "http://${SAFEEXEC_X5_HOST}:8789/healthz"
echo
curl -fsS --max-time 3 "http://${SAFEEXEC_X5_HOST}:8790/healthz"
echo
echo "End-to-end readiness"
curl -fsS --max-time 5 "${DASHBOARD_URL}/api/preflight"
echo
