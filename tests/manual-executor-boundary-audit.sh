#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
  shift
fi
if (( $# != 0 )); then
  echo "Usage: $0 [--strict]" >&2
  exit 2
fi

AGENTCTL="${AGENTCTL:-agentctl}"
DEPLOYED_AGENTD="${DEPLOYED_AGENTD:-/srv/agent-dev/platform/bin/agentd}"
SOURCE_AUDIT="$ROOT/tests/executor-boundary-source-audit.py"
FAIL=0
WARN=0

pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf 'FAIL %s\n' "$*"; FAIL=$((FAIL + 1)); }
section() { printf '\n== %s ==\n' "$*"; }

require_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command available: $1"
  else
    fail "required command unavailable: $1"
  fi
}

section "Host prerequisites"
for cmd in "$AGENTCTL" python3 jq systemctl id stat getfacl slirp4netns; do
  require_cmd "$cmd"
done

if (( FAIL != 0 )); then
  echo "Cannot continue live boundary audit because prerequisites are missing." >&2
  exit 1
fi

section "Broker identity"
SERVICE_USER=$(systemctl show agentd.service -p User --value 2>/dev/null || true)
[[ "$SERVICE_USER" == "agentdev" ]] && pass "agentd.service User=agentdev" || fail "agentd.service User=${SERVICE_USER:-<unset>}"
PRIMARY_GROUP=$(id -gn agentdev 2>/dev/null || true)
[[ "$PRIMARY_GROUP" == "agentdev" ]] && pass "agentdev primary group=agentdev" || fail "agentdev primary group=${PRIMARY_GROUP:-<unknown>}"

AGENT_GROUPS=$(id -nG agentdev 2>/dev/null || true)
if grep -qw agentdev-ops <<<"$AGENT_GROUPS"; then
  fail "agentdev is a member of agentdev-ops"
else
  pass "agentdev is not a member of agentdev-ops"
fi

section "Project namespace boundary"
if [[ -d /srv/agent-dev/projects ]]; then
  PROJECT_MODE=$(stat -c '%a' /srv/agent-dev/projects)
  PROJECT_OWNER=$(stat -c '%U:%G' /srv/agent-dev/projects)
  pass "/srv/agent-dev/projects exists mode=$PROJECT_MODE owner=$PROJECT_OWNER"
  ACL_LINE=$(getfacl -cp /srv/agent-dev/projects 2>/dev/null | awk -F: '$1=="user" && $2=="agentdev" {print $3; exit}')
  if [[ "$ACL_LINE" == "--x" ]]; then
    pass "agentdev project-namespace ACL is traverse-only (--x)"
  elif [[ -z "$ACL_LINE" ]]; then
    fail "agentdev project-namespace ACL is missing"
  else
    fail "agentdev project-namespace ACL is broader than traverse-only: $ACL_LINE"
  fi
else
  fail "/srv/agent-dev/projects is missing"
fi

section "Broker health and existing smoke boundary"
if "$AGENTCTL" ping; then
  pass "agentctl ping"
else
  fail "agentctl ping"
fi

if "$AGENTCTL" smoke; then
  pass "agentctl smoke (socket/credential absence, scoped provider state, explicit network boundary, and workspace ro/rw smoke)"
else
  fail "agentctl smoke"
fi

section "Provider status (no model task)"
if "$AGENTCTL" status; then
  pass "agentctl status"
else
  fail "agentctl status"
fi

section "Source-encoded executor boundary"
if [[ ! -f "$SOURCE_AUDIT" ]]; then
  fail "source audit script missing: $SOURCE_AUDIT"
elif [[ ! -f "$DEPLOYED_AGENTD" ]]; then
  fail "deployed agentd source missing: $DEPLOYED_AGENTD"
else
  set +e
  AGENTD_UNDER_TEST="$DEPLOYED_AGENTD" python3 "$SOURCE_AUDIT" --fail-on-warn
  AUDIT_RC=$?
  set -e
  case "$AUDIT_RC" in
    0) pass "deployed executor source audit has no WARN/FAIL" ;;
    2) warn "component status: deployed executor source audit PASS WITH WARNINGS (see component summary above)" ;;
    *) fail "deployed executor source audit rc=$AUDIT_RC" ;;
  esac
fi

section "Deployment drift"
LOCAL_AGENTD="$ROOT/platform-src/bin/agentd"
if [[ -f "$LOCAL_AGENTD" && -f "$DEPLOYED_AGENTD" ]]; then
  if cmp -s "$LOCAL_AGENTD" "$DEPLOYED_AGENTD"; then
    pass "deployed agentd matches repository source"
  else
    warn "deployed agentd differs from repository source; run bootstrap before interpreting runtime results"
  fi
else
  warn "cannot compare repository and deployed agentd"
fi

section "Audit result"
# Leaf audits own their numeric finding counts. This wrapper reports only
# component/overall status.
if (( FAIL != 0 )); then
  echo "EXECUTOR BOUNDARY AUDIT: FAIL"
  exit 1
fi
if (( WARN != 0 )); then
  if (( STRICT != 0 )); then
    echo "EXECUTOR BOUNDARY AUDIT: FAIL (warnings are fatal in --strict mode)"
    exit 2
  fi
  echo "EXECUTOR BOUNDARY AUDIT: PASS WITH WARNINGS"
else
  echo "EXECUTOR BOUNDARY AUDIT: PASS"
fi
