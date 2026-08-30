#!/usr/bin/env python3
"""Deterministic checks for the MA2-SEC-003 Cursor T5/T6 characterization harness."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.cursor import CursorDriver  # noqa: E402


def test_cursor_capability_remains_evidence_gated() -> None:
    caps = CursorDriver().capabilities()
    assert caps.native_sandbox
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert "provider_state_protection" not in caps.policy_capabilities


def test_probe_derives_split_cursor_state_contract() -> None:
    layouts = {layout.key: layout.mount for layout in CursorDriver().state_adapter().volumes}
    assert set(layouts) == {"state", "auth"}
    assert layouts["state"].target == "/root/.cursor"
    assert layouts["auth"].target == "/root/.config/cursor"

    source = (ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    required = (
        "CursorDriver().state_adapter().volumes",
        "STATE_PROBE_NAME",
        "AUTH_PROBE_NAME",
        "AGENTDEV_RUN_CURSOR_CREDENTIAL_T6",
        "AGENTDEV_SEC003_SECRET_TOKEN",
        "SEC003 T5 CURSOR VERSION",
        "SEC003 T5 LOGIN STATUS PASS",
        "SEC003 T5 AUTHENTICATED NATIVE-SANDBOX EXECUTION PASS",
        "SEC003 T6 NEGATIVE CONTROL PASS",
        "SEC003_T6_SCRIPT_STARTED",
        "SEC003_T6_NATIVE_SANDBOX_ACTIVE",
        "CURSOR_SANDBOX",
        "CURSOR_SANDBOX_LANDLOCK_STATUS",
        "fully_enforced|bubblewrap",
        "SEC003_T6_PROVIDER_STATE_DENIED",
        "SEC003_T6_PROVIDER_AUTH_DENIED",
        "SEC003_T6_TASK_ENV_FILTERED",
        "SEC003_T6_INHERITED_FD_DENIED",
        "/proc/$control_pid/environ",
        "/proc/$control_pid/cmdline",
        "/proc/$control_pid/fd",
        "/proc/$control_pid/root",
        "for link in root cwd; do",
        "/proc/$control_pid/mem",
        "SEC003_T6_TASK_LOCAL_PROCFS",
        "missing required observations",
        "synthetic secret sentinel leaked to task output",
        "SEC003 AUTHENTICATED T5/T6 CHARACTERIZATION PASS",
    )
    for marker in required:
        assert marker in source, marker

    assert source.count('"agent",') >= 2
    assert '"-p",' in source
    assert '"--trust",' in source
    assert '"--sandbox",' in source
    assert '"enabled",' in source
    assert '"--output-format",' in source
    assert '"text",' in source
    assert "FILLER_PROCESS_COUNT = 48" in source
    assert "--cap-add" not in source
    assert "--security-opt=unmask=/proc/*" not in source
    assert "danger-full-access" not in source


def test_negative_control_is_synthetic_and_fail_closed() -> None:
    source = (ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    assert "secrets.token_hex(32)" in source
    assert "TemporaryDirectory" in source
    assert "secret_file.write_text" in source
    assert "file_read.stdout != sentinel" in source
    assert "env_read.stdout != env_secret" in source
    assert "fd_read.stdout != sentinel" in source
    assert "redacted_failure_detail" in source
    assert "CURSOR_API_KEY" not in source
    assert "access_token" not in source.lower()


def test_headless_probe_keeps_shell_commands_sandbox_eligible() -> None:
    probe_path = ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py"
    spec = importlib.util.spec_from_file_location("cursor_sec003_probe_headless", probe_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    argv = module.cursor_headless_argv("probe")
    assert "--force" not in argv
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert "--trust" in argv
    assert "-p" in argv

    source = probe_path.read_text(encoding="utf-8")
    assert 'native|fully_enforced' in source
    assert 'native|bubblewrap' in source
    assert "authenticated task did not report an active Linux native sandbox" in source


def test_adversarial_wrapper_is_shell_syntax_valid() -> None:
    probe_path = ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py"
    spec = importlib.util.spec_from_file_location("cursor_sec003_probe_regression", probe_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    wrapper = module.adversarial_wrapper("/home/node/.config/cursor/.agentdev-sec003-auth-probe")
    checked = subprocess.run(
        ["bash", "-n"],
        input=wrapper,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert "shlex.quote(control_script)" in probe_path.read_text(encoding="utf-8")


def main() -> None:
    test_cursor_capability_remains_evidence_gated()
    test_probe_derives_split_cursor_state_contract()
    test_negative_control_is_synthetic_and_fail_closed()
    test_headless_probe_keeps_shell_commands_sandbox_eligible()
    test_adversarial_wrapper_is_shell_syntax_valid()
    print("Cursor credential confidentiality T5/T6 characterization regression checks passed")


if __name__ == "__main__":
    main()
