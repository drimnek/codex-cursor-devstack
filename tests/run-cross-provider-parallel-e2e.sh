#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E_SCRIPT="${AGENT_E2E_SCRIPT:-$ROOT/tests/manual-cross-provider-parallel-e2e.sh}"
PROJECT="${AGENT_E2E_PROJECT:-}"
RUN_ID="${AGENT_E2E_RUN_ID:-}"
REQUIRED="${AGENT_E2E_REQUIRED:-0}"

case "$REQUIRED" in
  0|1) ;;
  *)
    echo "cross-provider parallel E2E: FAIL (AGENT_E2E_REQUIRED must be 0 or 1)" >&2
    exit 2
    ;;
esac

if [[ -z "$PROJECT" ]]; then
  if [[ "$REQUIRED" == "1" ]]; then
    echo "cross-provider parallel E2E: FAIL (AGENT_E2E_PROJECT is not set)" >&2
    exit 1
  fi
  echo "cross-provider parallel E2E: SKIP (set AGENT_E2E_PROJECT to enable provider acceptance)"
  exit 0
fi

if [[ ! -x "$E2E_SCRIPT" ]]; then
  echo "cross-provider parallel E2E: FAIL (script is not executable: $E2E_SCRIPT)" >&2
  exit 1
fi

echo "cross-provider parallel E2E: RUN project=$PROJECT"

set +e
if [[ -n "$RUN_ID" ]]; then
  "$E2E_SCRIPT" "$PROJECT" "$RUN_ID"
  rc=$?
else
  "$E2E_SCRIPT" "$PROJECT"
  rc=$?
fi
set -e

if (( rc != 0 )); then
  echo "cross-provider parallel E2E: FAIL (rc=$rc)" >&2
  exit "$rc"
fi

echo "cross-provider parallel E2E: PASS"
