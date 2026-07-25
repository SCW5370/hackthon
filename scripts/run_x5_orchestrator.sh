#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="${SAFEEXEC_ROOT:-/opt/safeexec}"
PYTHON_BIN="${SAFEEXEC_PYTHON:-${SAFEEXEC_ROOT}/.venv/bin/python}"
LEGACY_URL="${LAB_LEGACY_URL:-http://30.201.220.34:8791}"

if [[ "${SAFEEXEC_AGENT_PROVIDER:-replay}" == "openai" && -z "${LLM_API_KEY:-}" ]]; then
  echo "LLM_API_KEY is required for the openai Agent provider" >&2
  exit 1
fi

orchestrator_args=(
  "${PYTHON_BIN}" -m orchestrator.orchestrator_http
  --host 0.0.0.0
  --port 8789
  --runtime-url http://127.0.0.1:8790
  --legacy-url "${LEGACY_URL}"
  --fact-mode external
  --agent-provider "${SAFEEXEC_AGENT_PROVIDER:-replay}"
  --require-trusted-work-order
  --recovery-delay 1.5
  --enable-testing
)
if [[ "${SAFEEXEC_ENABLE_UNSAFE_DEMO:-0}" == "1" ]]; then
  if [[ -z "${LAB_LEGACY_TOKEN:-}" ]]; then
    echo "LAB_LEGACY_TOKEN is required for the unsafe baseline" >&2
    exit 1
  fi
  orchestrator_args+=(--enable-unsafe-demo)
fi

exec "${orchestrator_args[@]}"
