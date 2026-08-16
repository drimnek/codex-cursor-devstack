#!/usr/bin/env bash
set -euo pipefail

if [[ "${AGENTDEV_RUN_LIFECYCLE_E2E:-0}" != "1" ]]; then
  echo "SKIP lifecycle E2E: set AGENTDEV_RUN_LIFECYCLE_E2E=1 to run against a deployed authenticated platform."
  exit 0
fi

AGENTCTL="${AGENTCTL:-agentctl}"
IMPLEMENT_PROVIDER="${AGENTDEV_IMPLEMENT_PROVIDER:-codex}"
REVIEW_PROVIDER="${AGENTDEV_REVIEW_PROVIDER:-cursor}"
CODEX_OUTER_ONLY="${AGENTDEV_CODEX_OUTER_ONLY:-0}"

case "$IMPLEMENT_PROVIDER" in
  codex|cursor) ;;
  *) echo "unsupported AGENTDEV_IMPLEMENT_PROVIDER: $IMPLEMENT_PROVIDER" >&2; exit 2 ;;
esac
case "$REVIEW_PROVIDER" in
  codex|cursor) ;;
  *) echo "unsupported AGENTDEV_REVIEW_PROVIDER: $REVIEW_PROVIDER" >&2; exit 2 ;;
esac
if [[ "$IMPLEMENT_PROVIDER" == "$REVIEW_PROVIDER" ]]; then
  echo "lifecycle E2E requires distinct implement/review providers" >&2
  exit 2
fi
case "$CODEX_OUTER_ONLY" in
  0|1) ;;
  *) echo "AGENTDEV_CODEX_OUTER_ONLY must be 0 or 1" >&2; exit 2 ;;
esac

command -v "$AGENTCTL" >/dev/null 2>&1 || {
  echo "agentctl executable not found: $AGENTCTL" >&2
  exit 2
}
command -v git >/dev/null 2>&1 || {
  echo "git is required" >&2
  exit 2
}
command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required" >&2
  exit 2
}

RUN_HELP="$("$AGENTCTL" run -h 2>&1)" || {
  echo "cannot inspect installed agentctl run interface" >&2
  exit 2
}
if ! grep -Eq '\{codex,cursor\}|\bprovider\b' <<<"$RUN_HELP"; then
  echo "installed agentctl run interface is incompatible with this lifecycle E2E runner: missing provider choices" >&2
  exit 2
fi
for token in project task prompt --readonly --outer-only; do
  if ! grep -Fq -- "$token" <<<"$RUN_HELP"; then
    echo "installed agentctl run interface is incompatible with this lifecycle E2E runner: missing $token" >&2
    exit 2
  fi
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PROJECT="${AGENTDEV_E2E_PROJECT:-e2e-lifecycle-${STAMP}-$$}"
SEQ_TASK="E2E-SEQ"
MERGE_TASK="E2E-PAR-MERGE"
ABORT_TASK="E2E-PAR-ABORT"
TMPDIR_E2E="$(mktemp -d)"
SOURCE="$TMPDIR_E2E/source"

cleanup() {
  rm -rf "$TMPDIR_E2E"
}
trap cleanup EXIT

run_agent() {
  local provider="$1"
  local task="$2"
  local mode="$3"
  local prompt="$4"
  local -a args=(run)

  # Keep run options before positional arguments. This is the documented v0.1
  # invocation form and is also accepted by older deployed agentctl parsers.
  if [[ "$mode" == "readonly" ]]; then
    args+=(--readonly)
  fi
  if [[ "$provider" == "codex" && "$CODEX_OUTER_ONLY" == "1" ]]; then
    args+=(--outer-only)
  fi
  args+=("$provider" "$PROJECT" "$task" "$prompt")
  "$AGENTCTL" "${args[@]}"
}

json_field() {
  local field="$1"
  python3 -c '
import json
import sys

field = sys.argv[1]
data = json.load(sys.stdin)
value = data.get(field)
if value is None:
    raise SystemExit(f"missing JSON field: {field}")
print(value)
' "$field"
}

project_head() {
  "$AGENTCTL" project-status "$PROJECT" | json_field head
}

expect_project_head_changed() {
  local before="$1"
  local context="$2"
  local after
  after="$(project_head)"
  if [[ "$after" == "$before" ]]; then
    echo "$context did not create a new integration commit" >&2
    if [[ "$IMPLEMENT_PROVIDER" == "codex" && "$CODEX_OUTER_ONLY" == "0" ]]; then
      echo "Codex may require AGENTDEV_CODEX_OUTER_ONLY=1 when its nested sandbox cannot create a user namespace inside the executor." >&2
    fi
    exit 1
  fi
}

task_field() {
  local task="$1"
  local field="$2"
  local payload
  payload="$("$AGENTCTL" task-list "$PROJECT")"
  printf '%s\n' "$payload" | python3 -c '
import json
import sys

task = sys.argv[1]
field = sys.argv[2]
records = json.load(sys.stdin)
matches = [record for record in records if record.get("task") == task]
if len(matches) != 1:
    raise SystemExit(f"expected one task record for {task}, got {len(matches)}")
value = matches[0].get(field)
if value is None:
    raise SystemExit(f"task {task} has no {field}")
print(value)
' "$task" "$field"
}

expect_task_head_changed() {
  local task="$1"
  local base="$2"
  local head
  head="$(task_field "$task" head_commit)"
  if [[ "$head" == "$base" ]]; then
    echo "task $task completed without creating a new commit" >&2
    exit 1
  fi
}

expect_task_status() {
  local task="$1"
  local expected="$2"
  local payload
  payload="$("$AGENTCTL" task-list "$PROJECT")"
  printf '%s\n' "$payload" | python3 -c '
import json
import sys

task = sys.argv[1]
expected = sys.argv[2]
records = json.load(sys.stdin)
matches = [record for record in records if record.get("task") == task]
if len(matches) != 1:
    raise SystemExit(f"expected one task record for {task}, got {len(matches)}")
actual = matches[0].get("status")
if actual != expected:
    raise SystemExit(f"task {task} status is {actual!r}, expected {expected!r}")
' "$task" "$expected"
}

echo "Lifecycle E2E project: $PROJECT"
echo "Implement provider: $IMPLEMENT_PROVIDER"
echo "Review provider: $REVIEW_PROVIDER"
echo "Codex outer-only: $CODEX_OUTER_ONLY"
echo "The project is intentionally retained under the deployed platform for post-run inspection."

"$AGENTCTL" ping
"$AGENTCTL" versions
"$AGENTCTL" smoke
"$AGENTCTL" status

mkdir -p "$SOURCE"
git -C "$SOURCE" init -q -b main
git -C "$SOURCE" config user.name "Agent Dev E2E"
git -C "$SOURCE" config user.email "agent-dev-e2e@localhost"
printf '# lifecycle e2e\n' > "$SOURCE/README.md"
git -C "$SOURCE" add README.md
git -C "$SOURCE" commit -qm "Initial lifecycle E2E state"
HUMAN_HEAD="$(git -C "$SOURCE" rev-parse HEAD)"

"$AGENTCTL" project-import "$PROJECT" "$SOURCE"
"$AGENTCTL" project-status "$PROJECT"

SEQ_TOKEN="sequential-${STAMP}-$$"
SEQ_START_JSON="$("$AGENTCTL" task-start "$PROJECT" "$SEQ_TASK")"
printf '%s\n' "$SEQ_START_JSON"
SEQ_BASE="$(printf '%s\n' "$SEQ_START_JSON" | json_field base_commit)"
run_agent \
  "$IMPLEMENT_PROVIDER" \
  "$SEQ_TASK" \
  write \
  "Create lifecycle-sequential.txt containing exactly '${SEQ_TOKEN}' followed by a newline. Do not modify any other tracked file. Run git status, git add lifecycle-sequential.txt, and commit the change with message 'E2E sequential lifecycle'. Finish only after the working tree is clean."
expect_project_head_changed "$SEQ_BASE" "sequential implementation"
run_agent \
  "$REVIEW_PROVIDER" \
  "$SEQ_TASK" \
  readonly \
  "Review the current task workspace. Confirm lifecycle-sequential.txt exists and contains '${SEQ_TOKEN}'. Inspect git status and the latest commit. Do not modify files, do not create commits, and exit successfully if the requested state is present."
"$AGENTCTL" task-complete "$PROJECT" "$SEQ_TASK"
expect_task_status "$SEQ_TASK" completed

MERGE_TOKEN="parallel-merge-${STAMP}-$$"
ABORT_TOKEN="parallel-abort-${STAMP}-$$"
MERGE_START_JSON="$("$AGENTCTL" task-start "$PROJECT" "$MERGE_TASK" --parallel --depends-on "$SEQ_TASK")"
printf '%s\n' "$MERGE_START_JSON"
MERGE_BASE="$(printf '%s\n' "$MERGE_START_JSON" | json_field base_commit)"
ABORT_START_JSON="$("$AGENTCTL" task-start "$PROJECT" "$ABORT_TASK" --parallel --depends-on "$SEQ_TASK")"
printf '%s\n' "$ABORT_START_JSON"
ABORT_BASE="$(printf '%s\n' "$ABORT_START_JSON" | json_field base_commit)"

run_agent \
  "$IMPLEMENT_PROVIDER" \
  "$MERGE_TASK" \
  write \
  "Create lifecycle-parallel-merge.txt containing exactly '${MERGE_TOKEN}' followed by a newline. Do not modify any other tracked file. Run git status, git add lifecycle-parallel-merge.txt, and commit the change with message 'E2E parallel merge lifecycle'. Finish only after the working tree is clean."
run_agent \
  "$REVIEW_PROVIDER" \
  "$ABORT_TASK" \
  write \
  "Create lifecycle-parallel-abort.txt containing exactly '${ABORT_TOKEN}' followed by a newline. Do not modify any other tracked file. Run git status, git add lifecycle-parallel-abort.txt, and commit the change with message 'E2E parallel abort lifecycle'. Finish only after the working tree is clean."

"$AGENTCTL" task-complete "$PROJECT" "$MERGE_TASK"
"$AGENTCTL" task-complete "$PROJECT" "$ABORT_TASK"
expect_task_status "$MERGE_TASK" completed
expect_task_status "$ABORT_TASK" completed
expect_task_head_changed "$MERGE_TASK" "$MERGE_BASE"
expect_task_head_changed "$ABORT_TASK" "$ABORT_BASE"

"$AGENTCTL" task-merge "$PROJECT" "$MERGE_TASK"
"$AGENTCTL" task-abort "$PROJECT" "$ABORT_TASK"
expect_task_status "$MERGE_TASK" merged
expect_task_status "$ABORT_TASK" aborted
"$AGENTCTL" project-status "$PROJECT"

EXPORT_JSON="$("$AGENTCTL" project-export "$PROJECT")"
mapfile -t EXPORT_FIELDS < <(
  printf '%s\n' "$EXPORT_JSON" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
print(data["bundle"])
print(data["integration_head"])
'
)
BUNDLE="${EXPORT_FIELDS[0]}"
EXPECTED_HEAD="${EXPORT_FIELDS[1]}"

if [[ ! -r "$BUNDLE" ]]; then
  echo "exported bundle is not readable: $BUNDLE" >&2
  exit 1
fi

git -C "$SOURCE" bundle verify "$BUNDLE" >/dev/null
git -C "$SOURCE" fetch -q "$BUNDLE" \
  refs/heads/agent/integration:refs/remotes/e2e/integration
ACTUAL_HEAD="$(git -C "$SOURCE" rev-parse refs/remotes/e2e/integration)"
if [[ "$ACTUAL_HEAD" != "$EXPECTED_HEAD" ]]; then
  echo "exported integration head mismatch: $ACTUAL_HEAD != $EXPECTED_HEAD" >&2
  exit 1
fi

SEQ_VALUE="$(git -C "$SOURCE" show refs/remotes/e2e/integration:lifecycle-sequential.txt)"
MERGE_VALUE="$(git -C "$SOURCE" show refs/remotes/e2e/integration:lifecycle-parallel-merge.txt)"
if [[ "$SEQ_VALUE" != "$SEQ_TOKEN" ]]; then
  echo "sequential result mismatch" >&2
  exit 1
fi
if [[ "$MERGE_VALUE" != "$MERGE_TOKEN" ]]; then
  echo "merged parallel result mismatch" >&2
  exit 1
fi
if git -C "$SOURCE" cat-file -e \
  refs/remotes/e2e/integration:lifecycle-parallel-abort.txt 2>/dev/null; then
  echo "aborted parallel result unexpectedly reached agent/integration" >&2
  exit 1
fi

if [[ "$(git -C "$SOURCE" rev-parse HEAD)" != "$HUMAN_HEAD" ]]; then
  echo "human source repository HEAD changed during lifecycle E2E" >&2
  exit 1
fi

echo "lifecycle E2E checks passed"
echo "project retained for inspection: $PROJECT"
echo "export bundle: $BUNDLE"
echo "integration head: $EXPECTED_HEAD"
