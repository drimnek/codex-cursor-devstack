#!/usr/bin/env python3
"""Deterministic checks for MA2-SEC-002 activation and deployed T5/T6 proof."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.codex import (  # noqa: E402
    CODEX_CREDENTIAL_PERMISSION_PROFILE,
    CODEX_POLICY_COMPILER_BASELINE,
    CODEX_PROVIDER_STATE_TARGET,
    CODEX_RUNTIME_GID,
    CODEX_RUNTIME_UID,
    CodexDriver,
    codex_credential_confidentiality_config_argv,
)


def test_pinned_permission_material() -> None:
    assert CODEX_POLICY_COMPILER_BASELINE == "0.147.0"
    assert CODEX_CREDENTIAL_PERMISSION_PROFILE == "agentdev_credential_confidentiality"
    assert CODEX_PROVIDER_STATE_TARGET == "/home/node/.codex"
    assert (CODEX_RUNTIME_UID, CODEX_RUNTIME_GID) == (1000, 1000)

    read = codex_credential_confidentiality_config_argv("read")
    write = codex_credential_confidentiality_config_argv("write")

    assert 'default_permissions="agentdev_credential_confidentiality"' in read
    assert 'permissions.agentdev_credential_confidentiality.extends=":read-only"' in read
    assert 'permissions.agentdev_credential_confidentiality.extends=":workspace"' in write
    deny = (
        'permissions.agentdev_credential_confidentiality.filesystem='
        '{ "/home/node/.codex" = "deny", "/home/node/.codex/**" = "deny" }'
    )
    assert deny in read
    assert deny in write
    assert 'shell_environment_policy.inherit="all"' in write
    assert "shell_environment_policy.ignore_default_excludes=false" in write
    assert 'history.persistence="none"' in write

    for bad in ("none", "readonly", "writable", ""):
        try:
            codex_credential_confidentiality_config_argv(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid workspace access accepted: {bad!r}")


def test_certified_capability_advertising() -> None:
    caps = CodexDriver().capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert caps.policy_capabilities == frozenset({"provider_state_protection"})


def test_probe_covers_authenticated_t5_and_required_t6_channels() -> None:
    source = (ROOT / "tests/e2e/codex-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    required_markers = (
        "codex", "exec",
        "T6_SCRIPT",
        "T6_OUTPUT",
        "sandbox_workspace_write.network_access=false",
        "SEC002 T5 LOGIN STATUS PASS",
        "SEC002 T5 AUTHENTICATED NATIVE-SANDBOX EXECUTION PASS",
        "SEC002 T6 NEGATIVE CONTROL PASS",
        "SEC002_T6_SCRIPT_STARTED",
        "codex_credential_confidentiality_config_argv",
        'PROBE_TARGET = f"{CODEX_PROVIDER_STATE_TARGET}/.agentdev-sec002-probe"',
        "--security-opt=unmask=/proc/*",
        "--user",
        "AGENTDEV_SEC002_SECRET_TOKEN",
        "CONTROL_PID_MARKER",
        "/proc/$control_pid/environ",
        "/proc/$control_pid/cmdline",
        "/proc/$control_pid/fd",
        "/proc/$control_pid/root",
        "for link in root cwd; do",
        '"/proc/$control_pid/$link"',
        "/proc/$control_pid/mem",
        "SEC002_T6_INHERITED_FD_DENIED",
        "SEC002_T6_TASK_LOCAL_PROCFS",
        "missing required observations",
        "synthetic secret sentinel leaked to task output",
        "SEC002 AUTHENTICATED T5/T6 PROOF PASS",
    )
    for marker in required_markers:
        assert marker in source, marker
    assert "redacted_failure_detail" in source
    assert "direct_sandbox_profile_config_argv" not in source
    assert '"sandbox",' not in source
    assert source.count('"exec",') >= 2

    assert "FILLER_PROCESS_COUNT = 48" in source
    assert "adversarial task did not create the observation output" in source
    assert "adversarial task output:" in source
    assert "AGENTDEV_SEC002_SENTINEL_SHA256" not in source
    assert "AGENTDEV_SEC002_PROBE_TARGET" not in source
    assert "export AGENTDEV_SEC002_CONTROL_PID" not in source
    assert "__SEC002_CONTROL_PID_MARKER__" in source
    assert 'network_mode=PROVIDER_NETWORK_MODE' in source
    assert 'network_mode="none"' in source
    assert "--cap-add" not in source
    assert "danger-full-access" not in source
    assert "sys.stderr.write" not in source


def test_negative_control_and_cleanup_are_synthetic() -> None:
    source = (ROOT / "tests/e2e/codex-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    assert "secrets.token_hex(32)" in source
    assert "TemporaryDirectory" in source
    assert "secret_file.write_text" in source
    assert "file_read.stdout != sentinel" in source
    assert "env_read.stdout != env_secret" in source
    assert "fd_read.stdout != sentinel" in source
    assert "OPENAI_API_KEY" not in source
    assert "access_token" not in source.lower()


def test_provider_helper_remains_declarative() -> None:
    helper_source = inspect.getsource(codex_credential_confidentiality_config_argv)
    assert "podman" not in helper_source.lower()
    assert "subprocess" not in helper_source.lower()


def main() -> None:
    test_pinned_permission_material()
    test_certified_capability_advertising()
    test_probe_covers_authenticated_t5_and_required_t6_channels()
    test_negative_control_and_cleanup_are_synthetic()
    test_provider_helper_remains_declarative()
    print("Codex credential confidentiality T5/T6 regression checks passed")


if __name__ == "__main__":
    main()
