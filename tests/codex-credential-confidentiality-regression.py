#!/usr/bin/env python3
"""Deterministic SEC-002 prerequisite checks for Codex credential isolation."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.codex import (
    CODEX_CREDENTIAL_PERMISSION_PROFILE,
    CODEX_POLICY_COMPILER_BASELINE,
    CODEX_PROVIDER_STATE_TARGET,
    CodexDriver,
    codex_credential_confidentiality_config_argv,
)


def test_pinned_permission_material() -> None:
    assert CODEX_POLICY_COMPILER_BASELINE == "0.147.0"
    assert CODEX_CREDENTIAL_PERMISSION_PROFILE == "agentdev_credential_confidentiality"
    assert CODEX_PROVIDER_STATE_TARGET == "/root/.codex"

    read = codex_credential_confidentiality_config_argv("read")
    write = codex_credential_confidentiality_config_argv("write")

    assert 'default_permissions="agentdev_credential_confidentiality"' in read
    assert 'permissions.agentdev_credential_confidentiality.extends=":read-only"' in read
    assert 'permissions.agentdev_credential_confidentiality.extends=":workspace"' in write
    deny = (
        'permissions.agentdev_credential_confidentiality.filesystem='
        '{ "/root/.codex" = "deny" }'
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


def test_no_premature_capability_advertising() -> None:
    caps = CodexDriver().capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert "provider_state_protection" not in caps.policy_capabilities


def test_probe_covers_required_channels() -> None:
    source = (ROOT / "tests/e2e/codex-credential-confidentiality-probe.py").read_text(
        encoding="utf-8"
    )
    required_markers = (
        "codex login status",
        "codex sandbox linux",
        "/root/.codex/.agentdev-sec002-probe",
        "AGENTDEV_SEC002_SECRET_TOKEN",
        "/proc/$pid/environ",
        "/proc/$pid/fd",
        "secret sentinel leaked to probe output",
        "SEC002 PREREQUISITE NOT MET",
    )
    for marker in required_markers:
        assert marker in source, marker

    helper_source = inspect.getsource(codex_credential_confidentiality_config_argv)
    assert "podman" not in helper_source.lower()
    assert "subprocess" not in helper_source.lower()


def main() -> None:
    test_pinned_permission_material()
    test_no_premature_capability_advertising()
    test_probe_covers_required_channels()
    print("Codex credential confidentiality prerequisite regression checks passed")


if __name__ == "__main__":
    main()
