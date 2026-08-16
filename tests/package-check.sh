#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m py_compile "$ROOT/platform-src/bin/agentctl" "$ROOT/platform-src/bin/agentd" "$ROOT/tests/security-regression.py" "$ROOT/tests/cursor-policy-reconciliation-regression.py"
rm -rf "$ROOT/platform-src/bin/__pycache__" "$ROOT/tests/__pycache__"
bash -n "$ROOT/bootstrap.sh" "$ROOT/tests/git-model-smoke.sh"
python3 - <<PY
from pathlib import Path
import json
root=Path("$ROOT")
json.load(open(root/'platform-src/seed/cursor/cli-config.json'))
json.load(open(root/'ansible/templates/platform.json.j2'.replace('.j2','.j2'))) if False else None
bootstrap=(root/'bootstrap.sh').read_text()
assert '--check' in bootstrap
assert '--syntax-check' in bootstrap
flow='preflight\nensure_ansible\nansible_syntax_check'
assert flow in bootstrap, 'bootstrap must preflight, install/verify Ansible, then syntax-check'
assert 'if [[ "\$MODE" == "check" ]]' in bootstrap
assert 'run_deployment' in bootstrap
playbook=(root/'ansible/playbook.yml').read_text()
runuser_marker='- runuser'
assert runuser_marker in playbook, 'expected agent identity validation command is missing'
runuser_block=playbook[playbook.index(runuser_marker):]
assert 'chdir: "{{ agent_home }}"' in runuser_block, 'runuser-based agent validation must use an agent-accessible working directory'
service=(root/'ansible/templates/agentd.service.j2').read_text()
assert 'WorkingDirectory={{ agent_home }}' in service, 'agentd service must have an explicit accessible working directory'
print('package syntax, bootstrap flow, and agent working-directory checks passed')
PY
"$ROOT/tests/git-model-smoke.sh"
python3 "$ROOT/tests/security-regression.py"
python3 "$ROOT/tests/cursor-policy-reconciliation-regression.py"
python3 "$ROOT/tests/package-baseline-regression.py"
python3 "$ROOT/tests/broker-rpc-contract-regression.py"
python3 "$ROOT/tests/provider-invocation-regression.py"
python3 "$ROOT/tests/parallel-lifecycle-regression.py"
python3 "$ROOT/tests/locking-concurrency-regression.py"
python3 "$ROOT/tests/dependency-semantics-regression.py"
python3 "$ROOT/tests/project-list-regression.py"
python3 "$ROOT/tests/provider-e2e-runner-regression.py"
python3 "$ROOT/tests/provider-state-layout-regression.py"
python3 "$ROOT/tests/executor-network-contract-regression.py"
python3 "$ROOT/tests/executor-boundary-source-audit.py"
"$ROOT/tests/run-cross-provider-parallel-e2e.sh"
# Cursor installer layout regression: install directly under the immutable
# executor prefix and preserve the installer-created launcher chain.
CURSOR_CF="$ROOT/platform-src/containers/Containerfile.cursor"
grep -Fq 'HOME=/opt/cursor-cli' "$CURSOR_CF"
grep -Fq 'test -L /opt/cursor-cli/.local/bin/agent' "$CURSOR_CF"
grep -Fq 'test -x /opt/cursor-cli/.local/bin/agent' "$CURSOR_CF"
grep -Fq 'test -x "$(readlink -f /opt/cursor-cli/.local/bin/agent)"' "$CURSOR_CF"
grep -Fq 'ln -s /opt/cursor-cli/.local/bin/agent /usr/local/bin/agent' "$CURSOR_CF"
if grep -Fq 'cp -a /root/.local/. /opt/cursor-cli/' "$CURSOR_CF"; then
  echo "Cursor Containerfile must install directly under /opt/cursor-cli instead of relocating /root/.local." >&2
  exit 1
fi
if grep -Fq 'cp /root/.local/bin/agent /usr/local/bin/agent' "$CURSOR_CF"; then
  echo "Cursor Containerfile must not copy the agent launcher without its companion installation tree." >&2
  exit 1
fi
