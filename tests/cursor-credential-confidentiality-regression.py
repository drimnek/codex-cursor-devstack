#!/usr/bin/env python3
"""Deterministic checks for the MA2-SEC-003 Cursor T5/T6 characterization harness."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.cursor import (  # noqa: E402
    CURSOR_AUTH_TARGET,
    CURSOR_CONTROL_ISOLATION,
    CURSOR_CREDENTIAL_DENY_PATTERNS,
    CURSOR_CREDENTIAL_DENY_SEED,
    CURSOR_CREDENTIAL_DENY_TARGET,
    CURSOR_RUNTIME_GID,
    CURSOR_RUNTIME_UID,
    CURSOR_SANDBOX_ISOLATION,
    CURSOR_STATE_TARGET,
    CursorDriver,
)
from agentdev.runtime.podman import runtime_isolation_args  # noqa: E402


def test_certified_capability_advertising() -> None:
    caps = CursorDriver().capabilities()
    assert caps.native_sandbox
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert caps.policy_capabilities == frozenset({"provider_state_protection"})


def test_cursor_credential_deny_policy_is_trusted_and_read_only() -> None:
    adapter = CursorDriver().state_adapter()
    assert tuple(
        (item.seed_relative_path, item.target, item.read_only) for item in adapter.policy_mounts
    ) == ((CURSOR_CREDENTIAL_DENY_SEED, CURSOR_CREDENTIAL_DENY_TARGET, True),)

    seed = ROOT / "platform-src" / "seed" / "cursor" / CURSOR_CREDENTIAL_DENY_SEED
    patterns = tuple(
        line.strip()
        for line in seed.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert patterns == CURSOR_CREDENTIAL_DENY_PATTERNS
    assert CURSOR_CREDENTIAL_DENY_PATTERNS == ("/.cursor/", "/.config/cursor/")


def test_probe_derives_split_cursor_state_contract() -> None:
    layouts = {layout.key: layout for layout in CursorDriver().state_adapter().volumes}
    assert set(layouts) == {"state", "auth"}
    assert layouts["state"].mount.target == CURSOR_STATE_TARGET
    assert layouts["auth"].mount.target == CURSOR_AUTH_TARGET
    for layout in layouts.values():
        assert (layout.owner_uid, layout.owner_gid) == (CURSOR_RUNTIME_UID, CURSOR_RUNTIME_GID)

    assert runtime_isolation_args(CURSOR_CONTROL_ISOLATION) == ["--user", "1000:1000"]
    assert runtime_isolation_args(CURSOR_SANDBOX_ISOLATION) == [
        "--user", "1000:1000",
        "--security-opt=unmask=/proc/*",
    ]

    source = (ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    required = (
        "CursorDriver().state_adapter().volumes",
        "CursorDriver().state_adapter().policy_mounts",
        "cursor_policy_mount_args",
        "CURSOR_CREDENTIAL_DENY_SEED",
        "CURSOR_CREDENTIAL_DENY_TARGET",
        "CURSOR_CONTROL_ISOLATION",
        "CURSOR_SANDBOX_ISOLATION",
        "runtime_isolation_args",
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
    assert "danger-full-access" not in source
    assert 'subprocess.run(["git", "init", "-q", str(workspace)], check=True)' in source


def test_probe_mounts_deployed_credential_deny_policy() -> None:
    probe_path = ROOT / "tests/e2e/cursor-credential-confidentiality-probe.py"
    spec = importlib.util.spec_from_file_location("cursor_sec003_probe_policy", probe_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_dir = root / "platform" / "seed" / "cursor"
        seed_dir.mkdir(parents=True)
        seed = seed_dir / CURSOR_CREDENTIAL_DENY_SEED
        seed.write_text("\n".join(CURSOR_CREDENTIAL_DENY_PATTERNS) + "\n", encoding="utf-8")
        args = module.cursor_policy_mount_args({"root": str(root)})
        assert args == ["-v", f"{seed}:{CURSOR_CREDENTIAL_DENY_TARGET}:ro"]


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
    test_certified_capability_advertising()
    test_cursor_credential_deny_policy_is_trusted_and_read_only()
    test_probe_derives_split_cursor_state_contract()
    test_probe_mounts_deployed_credential_deny_policy()
    test_negative_control_is_synthetic_and_fail_closed()
    test_headless_probe_keeps_shell_commands_sandbox_eligible()
    test_adversarial_wrapper_is_shell_syntax_valid()
    print("Cursor credential confidentiality T5/T6 characterization regression checks passed")


if __name__ == "__main__":
    main()
