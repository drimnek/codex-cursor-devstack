#!/usr/bin/env python3
"""Regression coverage for the SEC-002 nested-sandbox runtime checkpoint."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.codex import (
    CODEX_CONTROL_ISOLATION,
    CODEX_PROVIDER_STATE_TARGET,
    CODEX_RUNTIME_GID,
    CODEX_RUNTIME_UID,
    CODEX_SANDBOX_ISOLATION,
    CodexDriver,
)
from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.runtime.podman import runtime_isolation_args


def load_agentd():
    path = ROOT / "platform-src/bin/agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_sec002_runtime", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.LOG.disabled = True
    return module


def expect_value_error(fn) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_runtime_isolation_model_and_podman_translation() -> None:
    default = RuntimeIsolationRequirements()
    assert default.as_dict() == {
        "uid": None,
        "gid": None,
        "nested_sandbox_bootstrap": False,
    }
    assert runtime_isolation_args(default) == []

    control = RuntimeIsolationRequirements(uid=1000, gid=1000)
    assert runtime_isolation_args(control) == ["--user", "1000:1000"]

    nested = RuntimeIsolationRequirements(
        uid=1000,
        gid=1000,
        nested_sandbox_bootstrap=True,
    )
    args = runtime_isolation_args(nested)
    assert args == [
        "--user", "1000:1000",
        "--security-opt=unmask=/proc/*",
    ]
    assert not any(item.startswith("--cap-add") for item in args)

    expect_value_error(lambda: RuntimeIsolationRequirements(uid=1000))
    expect_value_error(
        lambda: RuntimeIsolationRequirements(
            uid=0,
            gid=0,
            nested_sandbox_bootstrap=True,
        )
    )


def test_codex_requests_nonroot_nested_sandbox_without_certifying_security() -> None:
    driver = CodexDriver()
    assert CODEX_PROVIDER_STATE_TARGET == "/home/node/.codex"
    assert (CODEX_RUNTIME_UID, CODEX_RUNTIME_GID) == (1000, 1000)
    assert CODEX_CONTROL_ISOLATION == RuntimeIsolationRequirements(uid=1000, gid=1000)
    assert CODEX_SANDBOX_ISOLATION.nested_sandbox_bootstrap

    layout = driver.state_adapter().primary()
    assert layout.mount.target == CODEX_PROVIDER_STATE_TARGET
    assert (layout.owner_uid, layout.owner_gid) == (1000, 1000)
    assert driver.state_adapter().policy_mounts[0].target == (
        f"{CODEX_PROVIDER_STATE_TARGET}/config.toml"
    )

    auth = driver.auth_spec()
    status = driver.auth_status_spec()
    assert auth.runtime_isolation == CODEX_CONTROL_ISOLATION
    assert status.runtime_isolation == CODEX_CONTROL_ISOLATION

    sandboxed = driver.compile_policy({"readonly": False, "outer_only": False})
    outer_only = driver.compile_policy({"readonly": False, "outer_only": True})
    assert sandboxed.runtime_isolation == CODEX_SANDBOX_ISOLATION
    assert outer_only.runtime_isolation == CODEX_CONTROL_ISOLATION

    caps = driver.capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "provider_state_protection" in caps.policy_capabilities
    assert "hardened" not in caps.security_classes


def test_migration_script_does_not_chown_inside_container() -> None:
    agentd = load_agentd()
    script = agentd._migration_script(CodexDriver().state_adapter())
    assert "chown " not in script
    assert ".agent-dev-owner-1000-1000" not in script


def test_container_and_generic_runtime_boundaries() -> None:
    containerfile = (PLATFORM / "containers/Containerfile.codex").read_text(
        encoding="utf-8"
    )
    assert "ENV CODEX_HOME=/home/node/.codex" in containerfile

    isolation_source = (PLATFORM / "agentdev/execution/isolation.py").read_text(
        encoding="utf-8"
    ).lower()
    podman_source = (PLATFORM / "agentdev/runtime/podman.py").read_text(
        encoding="utf-8"
    ).lower()
    for source in (isolation_source, podman_source):
        assert "codex" not in source
        assert "cursor" not in source
    assert "--cap-add" not in podman_source


if __name__ == "__main__":
    test_runtime_isolation_model_and_podman_translation()
    test_codex_requests_nonroot_nested_sandbox_without_certifying_security()
    test_migration_script_does_not_chown_inside_container()
    test_container_and_generic_runtime_boundaries()
    print("SEC-002 Codex runtime isolation regression checks passed")
