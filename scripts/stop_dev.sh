#!/usr/bin/env bash
set -euo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${SAFEEXEC_ROOT}/scripts/stop_stack.sh"
