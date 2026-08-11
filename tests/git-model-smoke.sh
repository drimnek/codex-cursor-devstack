#!/usr/bin/env bash
set -euo pipefail
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

git init -q -b main "$T/main"
git -C "$T/main" config user.email test@example.invalid
git -C "$T/main" config user.name Test
echo base > "$T/main/file.txt"
git -C "$T/main" add file.txt
git -C "$T/main" commit -qm base

git clone -q --no-local "$T/main" "$T/agent"
git -C "$T/agent" config user.email agent@example.invalid
git -C "$T/agent" config user.name Agent
git -C "$T/agent" remote remove origin
git -C "$T/agent" switch -qc agent/integration

# The agent object store must not use hardlinks into the human repository.
OBJ_REL="$(cd "$T/main" && find .git/objects -type f | head -n1 | sed 's#^./##')"
if [[ -n "$OBJ_REL" && -e "$T/agent/$OBJ_REL" ]]; then
  test "$(stat -c '%d:%i' "$T/main/$OBJ_REL")" != "$(stat -c '%d:%i' "$T/agent/$OBJ_REL")"
fi

# Human -> agent synchronization is an explicit data-only bundle transfer.
echo human-update > "$T/main/human.txt"
git -C "$T/main" add human.txt
git -C "$T/main" commit -qm human-update
git -C "$T/main" bundle create "$T/main.bundle" main
git -C "$T/agent" fetch "$T/main.bundle" refs/heads/main:refs/remotes/human-main/main
git -C "$T/agent" merge --ff-only -q refs/remotes/human-main/main

# Sequential requirement on the integration branch.
printf 'sequential\n' >> "$T/agent/file.txt"
git -C "$T/agent" add file.txt
git -C "$T/agent" commit -qm REQ-001

# Parallel requirements from the same integration point.
git -C "$T/agent" worktree add -qb agent/REQ-002 "$T/REQ-002" agent/integration
git -C "$T/agent" worktree add -qb agent/REQ-003 "$T/REQ-003" agent/integration
printf 'two\n' > "$T/REQ-002/two.txt"
git -C "$T/REQ-002" add two.txt
git -C "$T/REQ-002" commit -qm REQ-002
printf 'three\n' > "$T/REQ-003/three.txt"
git -C "$T/REQ-003" add three.txt
git -C "$T/REQ-003" commit -qm REQ-003

HEAD2="$(git -C "$T/REQ-002" rev-parse HEAD)"
HEAD3="$(git -C "$T/REQ-003" rev-parse HEAD)"
git -C "$T/agent" merge --no-ff --no-edit -q "$HEAD2"
git -C "$T/agent" worktree remove "$T/REQ-002"
git -C "$T/agent" branch -d agent/REQ-002 >/dev/null
git -C "$T/agent" merge --no-ff --no-edit -q "$HEAD3"
git -C "$T/agent" worktree remove "$T/REQ-003"
git -C "$T/agent" branch -d agent/REQ-003 >/dev/null

test -f "$T/agent/human.txt"
test -f "$T/agent/two.txt"
test -f "$T/agent/three.txt"
test "$(git -C "$T/agent" branch --show-current)" = "agent/integration"
echo "git model smoke test passed"
