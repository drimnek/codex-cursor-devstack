#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  tests/manual-cross-provider-parallel-e2e.sh PROJECT [RUN_ID]

Runs one real cross-provider parallel acceptance flow:
  Cursor write task
  Codex write task
  complete both
  merge both
  verify exported integration bundle
  Codex read-only final review

RUN_ID defaults to a UTC timestamp plus the shell PID.
This script intentionally is not part of package-check.sh because it consumes
provider quota and requires valid Cursor/Codex authentication.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

assert_eq() {
  local actual=$1
  local expected=$2
  local message=$3
  [[ "$actual" == "$expected" ]] || die "$message: expected '$expected', got '$actual'"
}

json_task_field() {
  local json=$1
  local task=$2
  local field=$3
  jq -er --arg task "$task" --arg field "$field" '
    first(.[] | select(.task == $task)) | .[$field]
  ' <<<"$json"
}

if (( $# < 1 || $# > 2 )); then
  usage
  exit 2
fi

PROJECT=$1
RUN_ID=${2:-"$(date -u +%Y%m%dT%H%M%SZ)-$$"}
AGENTCTL=${AGENTCTL:-agentctl}

[[ "$PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
  || die "invalid project name: $PROJECT"
[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || die "RUN_ID contains unsupported characters: $RUN_ID"

CURSOR_TASK="REQ-PAR-CURSOR-${RUN_ID}"
CODEX_TASK="REQ-PAR-CODEX-${RUN_ID}"
CURSOR_FILE="parallel-cursor-${RUN_ID}.txt"
CODEX_FILE="parallel-codex-${RUN_ID}.txt"
CURSOR_LINE="Cursor parallel E2E succeeded: ${RUN_ID}"
CODEX_LINE="Codex parallel E2E succeeded: ${RUN_ID}"

(( ${#CURSOR_TASK} <= 64 )) || die "generated Cursor task id exceeds 64 characters"
(( ${#CODEX_TASK} <= 64 )) || die "generated Codex task id exceeds 64 characters"

require_cmd "$AGENTCTL"
require_cmd jq
require_cmd git
require_cmd cmp
require_cmd mktemp

STAGE="preflight"
on_error() {
  local rc=$?
  echo >&2
  echo "Cross-provider parallel E2E failed during stage: $STAGE" >&2
  echo "Project: $PROJECT" >&2
  echo "Cursor task: $CURSOR_TASK" >&2
  echo "Codex task: $CODEX_TASK" >&2
  echo >&2
  echo "Current task metadata:" >&2
  "$AGENTCTL" task-list "$PROJECT" >&2 || true
  echo >&2
  echo "Do not rerun with the same RUN_ID until the failed tasks are inspected." >&2
  echo "Active/completed unmerged parallel tasks can be cleaned with:" >&2
  echo "  $AGENTCTL task-abort $PROJECT $CURSOR_TASK" >&2
  echo "  $AGENTCTL task-abort $PROJECT $CODEX_TASK" >&2
  exit "$rc"
}
trap on_error ERR

echo "== Cross-provider parallel E2E =="
echo "Project:     $PROJECT"
echo "Run ID:      $RUN_ID"
echo "Cursor task: $CURSOR_TASK"
echo "Codex task:  $CODEX_TASK"
echo

STAGE="preflight"
"$AGENTCTL" ping >/dev/null
"$AGENTCTL" status

PROJECT_STATUS=$("$AGENTCTL" project-status "$PROJECT")
assert_eq "$(jq -er '.branch' <<<"$PROJECT_STATUS")" "agent/integration" \
  "project must be on agent/integration before the E2E"
assert_eq "$(jq -er '.clean | tostring' <<<"$PROJECT_STATUS")" "true" \
  "integration workspace must be clean before the E2E"

PENDING_COUNT=$(jq -er '
  [
    .tasks[]
    | select(
        .status == "active"
        or (.mode == "parallel" and .status == "completed")
      )
  ] | length
' <<<"$PROJECT_STATUS")
assert_eq "$PENDING_COUNT" "0" \
  "project already has active or completed-unmerged tasks"

echo
echo "== Start two parallel tasks =="

STAGE="start Cursor parallel task"
"$AGENTCTL" task-start "$PROJECT" "$CURSOR_TASK" --parallel >/dev/null

STAGE="start Codex parallel task"
"$AGENTCTL" task-start "$PROJECT" "$CODEX_TASK" --parallel >/dev/null

STAGE="validate parallel task metadata"
TASKS=$("$AGENTCTL" task-list "$PROJECT")

assert_eq "$(json_task_field "$TASKS" "$CURSOR_TASK" mode)" "parallel" \
  "Cursor task mode"
assert_eq "$(json_task_field "$TASKS" "$CODEX_TASK" mode)" "parallel" \
  "Codex task mode"
assert_eq "$(json_task_field "$TASKS" "$CURSOR_TASK" status)" "active" \
  "Cursor task status"
assert_eq "$(json_task_field "$TASKS" "$CODEX_TASK" status)" "active" \
  "Codex task status"

CURSOR_BASE=$(json_task_field "$TASKS" "$CURSOR_TASK" base_commit)
CODEX_BASE=$(json_task_field "$TASKS" "$CODEX_TASK" base_commit)
CURSOR_WS=$(json_task_field "$TASKS" "$CURSOR_TASK" workspace)
CODEX_WS=$(json_task_field "$TASKS" "$CODEX_TASK" workspace)
CURSOR_BRANCH=$(json_task_field "$TASKS" "$CURSOR_TASK" branch)
CODEX_BRANCH=$(json_task_field "$TASKS" "$CODEX_TASK" branch)

assert_eq "$CURSOR_BASE" "$CODEX_BASE" \
  "parallel tasks must start from the same integration base"
[[ "$CURSOR_WS" != "$CODEX_WS" ]] || die "parallel tasks unexpectedly share a workspace"
[[ "$CURSOR_BRANCH" != "$CODEX_BRANCH" ]] || die "parallel tasks unexpectedly share a branch"

echo "Parallel task metadata: PASS"
echo "  common base: $CURSOR_BASE"
echo "  Cursor worktree: $CURSOR_WS"
echo "  Codex worktree:  $CODEX_WS"

read -r -d '' CURSOR_PROMPT <<EOF || true
You are executing $CURSOR_TASK in its broker-provided parallel worktree.

Create exactly one repository-root file named:
$CURSOR_FILE

Its complete contents must be exactly this single line:
$CURSOR_LINE

Requirements:
- Do not modify any other tracked file.
- Use git status and git diff to verify the change.
- Commit exactly that file.
- Use this exact commit message:
  Complete Cursor parallel E2E $RUN_ID
- Finish only when the task working tree is clean.
EOF

echo
echo "== Cursor write task =="
STAGE="Cursor write execution"
"$AGENTCTL" run cursor "$PROJECT" "$CURSOR_TASK" "$CURSOR_PROMPT"

read -r -d '' CODEX_PROMPT <<EOF || true
You are executing $CODEX_TASK in its broker-provided parallel worktree.

Create exactly one repository-root file named:
$CODEX_FILE

Its complete contents must be exactly this single line:
$CODEX_LINE

Requirements:
- Do not modify any other tracked file.
- Use git status and git diff to verify the change.
- Commit exactly that file.
- Use this exact commit message:
  Complete Codex parallel E2E $RUN_ID
- Finish only when the task working tree is clean.
EOF

echo
echo "== Codex write task =="
STAGE="Codex write execution"
"$AGENTCTL" run --outer-only codex "$PROJECT" "$CODEX_TASK" "$CODEX_PROMPT"

echo
echo "== Complete both tasks =="

STAGE="complete Cursor task"
"$AGENTCTL" task-complete "$PROJECT" "$CURSOR_TASK" >/dev/null

STAGE="complete Codex task"
"$AGENTCTL" task-complete "$PROJECT" "$CODEX_TASK" >/dev/null

STAGE="validate completed metadata"
TASKS=$("$AGENTCTL" task-list "$PROJECT")

assert_eq "$(json_task_field "$TASKS" "$CURSOR_TASK" status)" "completed" \
  "Cursor task status after completion"
assert_eq "$(json_task_field "$TASKS" "$CODEX_TASK" status)" "completed" \
  "Codex task status after completion"

CURSOR_HEAD=$(json_task_field "$TASKS" "$CURSOR_TASK" head_commit)
CODEX_HEAD=$(json_task_field "$TASKS" "$CODEX_TASK" head_commit)
[[ -n "$CURSOR_HEAD" ]] || die "Cursor completed task has no head_commit"
[[ -n "$CODEX_HEAD" ]] || die "Codex completed task has no head_commit"
[[ "$CURSOR_HEAD" != "$CODEX_HEAD" ]] || die "parallel tasks unexpectedly recorded the same head_commit"

echo "Completion metadata: PASS"
echo "  Cursor head: $CURSOR_HEAD"
echo "  Codex head:  $CODEX_HEAD"

echo
echo "== Merge both tasks =="

STAGE="merge Cursor task"
"$AGENTCTL" task-merge "$PROJECT" "$CURSOR_TASK" >/dev/null

STAGE="merge Codex task"
"$AGENTCTL" task-merge "$PROJECT" "$CODEX_TASK" >/dev/null

STAGE="validate merged metadata"
TASKS=$("$AGENTCTL" task-list "$PROJECT")

assert_eq "$(json_task_field "$TASKS" "$CURSOR_TASK" status)" "merged" \
  "Cursor task status after merge"
assert_eq "$(json_task_field "$TASKS" "$CODEX_TASK" status)" "merged" \
  "Codex task status after merge"

CURSOR_MERGE=$(json_task_field "$TASKS" "$CURSOR_TASK" merge_commit)
CODEX_MERGE=$(json_task_field "$TASKS" "$CODEX_TASK" merge_commit)
[[ -n "$CURSOR_MERGE" ]] || die "Cursor merged task has no merge_commit"
[[ -n "$CODEX_MERGE" ]] || die "Codex merged task has no merge_commit"

PROJECT_STATUS=$("$AGENTCTL" project-status "$PROJECT")
assert_eq "$(jq -er '.branch' <<<"$PROJECT_STATUS")" "agent/integration" \
  "project branch after merges"
assert_eq "$(jq -er '.clean | tostring' <<<"$PROJECT_STATUS")" "true" \
  "integration workspace after merges"
MERGED_HEAD=$(jq -er '.head' <<<"$PROJECT_STATUS")

echo "Merge lifecycle: PASS"
echo "  integration head: $MERGED_HEAD"

echo
echo "== Verify exported integration bundle =="

STAGE="project export"
EXPORT_JSON=$("$AGENTCTL" project-export "$PROJECT")
BUNDLE=$(jq -er '.bundle' <<<"$EXPORT_JSON")
EXPORTED_HEAD=$(jq -er '.integration_head' <<<"$EXPORT_JSON")
assert_eq "$EXPORTED_HEAD" "$MERGED_HEAD" \
  "exported integration head"

[[ -r "$BUNDLE" ]] || die "exported bundle is not readable: $BUNDLE"

REVIEW_TMP=$(mktemp -d)
cleanup_review_tmp() {
  rm -rf "$REVIEW_TMP"
}
trap cleanup_review_tmp EXIT

git -C "$REVIEW_TMP" init -q
git -C "$REVIEW_TMP" bundle verify "$BUNDLE" >/dev/null
git -C "$REVIEW_TMP" fetch -q \
  "$BUNDLE" \
  "refs/heads/agent/integration:refs/remotes/export/integration"

printf '%s\n' "$CURSOR_LINE" >"$REVIEW_TMP/expected-cursor"
printf '%s\n' "$CODEX_LINE" >"$REVIEW_TMP/expected-codex"

git -C "$REVIEW_TMP" show \
  "refs/remotes/export/integration:$CURSOR_FILE" >"$REVIEW_TMP/actual-cursor"
git -C "$REVIEW_TMP" show \
  "refs/remotes/export/integration:$CODEX_FILE" >"$REVIEW_TMP/actual-codex"

cmp -s "$REVIEW_TMP/expected-cursor" "$REVIEW_TMP/actual-cursor" \
  || die "Cursor E2E file content does not match in exported integration"
cmp -s "$REVIEW_TMP/expected-codex" "$REVIEW_TMP/actual-codex" \
  || die "Codex E2E file content does not match in exported integration"

echo "Exported integration content: PASS"

read -r -d '' REVIEW_PROMPT <<EOF || true
Perform a final read-only review of the current agent/integration workspace.

Do not modify files and do not create commits.

Verify all of the following:
1. git branch --show-current reports agent/integration.
2. git status --short is clean.
3. $CURSOR_FILE exists and contains exactly:
   $CURSOR_LINE
4. $CODEX_FILE exists and contains exactly:
   $CODEX_LINE
5. git history contains the integrated results of both parallel tasks.

If every check passes, end your response with exactly:
CROSS-PROVIDER PARALLEL E2E: PASS

If any check fails, end with:
CROSS-PROVIDER PARALLEL E2E: FAIL
and explain the failed check.
EOF

echo
echo "== Codex final read-only review =="

STAGE="Codex read-only final review"
"$AGENTCTL" run --readonly --outer-only codex "$PROJECT" "$CODEX_TASK" "$REVIEW_PROMPT"

STAGE="verify review did not mutate integration"
FINAL_STATUS=$("$AGENTCTL" project-status "$PROJECT")
assert_eq "$(jq -er '.head' <<<"$FINAL_STATUS")" "$MERGED_HEAD" \
  "read-only review must not move integration HEAD"
assert_eq "$(jq -er '.clean | tostring' <<<"$FINAL_STATUS")" "true" \
  "read-only review must leave integration clean"

trap - ERR

echo
echo "== Acceptance result =="
echo "Parallel task creation:             PASS"
echo "Provider-specific worktree routing: PASS"
echo "Cursor write + commit:              PASS"
echo "Codex write + commit:               PASS"
echo "Task completion metadata:           PASS"
echo "Cursor merge:                       PASS"
echo "Codex merge:                        PASS"
echo "Exported integration verification:  PASS"
echo "Codex read-only final review:       PASS"
echo "Integration unchanged by review:    PASS"
echo
echo "CROSS-PROVIDER PARALLEL E2E: PASS"
