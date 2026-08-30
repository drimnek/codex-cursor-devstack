#!/usr/bin/env python3
"""Regression coverage for the SEC-003 Cursor nested-sandbox runtime checkpoint."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.cursor import (
    CURSOR_AUTH_TARGET,
    CURSOR_CONTROL_ISOLATION,
    CURSOR_RUNTIME_GID,
    CURSOR_RUNTIME_HOME,
    CURSOR_RUNTIME_UID,
    CURSOR_SANDBOX_ISOLATION,
    CURSOR_STATE_TARGET,
    CursorDriver,
)
from agentdev.core.models import TaskContext
from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.policy.schema import ExecutionPolicy
from agentdev.runtime.podman import runtime_isolation_args


def policy(*, network: str = "deny", sandbox: bool = True) -> ExecutionPolicy:
    return ExecutionPolicy.from_dict({
        "version": 1,
        "workspace": {"access": "write"},
        "reference": {"access": "read"},
        "filesystem": {"external": "deny"},
        "network": {"task_shell": {"mode": network, "destinations": []}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"read": True, "commit": True, "push": False},
        "sandbox": {"required": sandbox},
        "resources": {"cpu": 4, "memory": "8g", "pids": 1024},
        "security_class": "compatibility",
    })


def context() -> TaskContext:
    return TaskContext(
        project="demo",
        task="SEC-003",
        mode="integration",
        status="active",
        metadata_path=Path("/srv/agent-dev/projects/demo/tasks/SEC-003.json"),
        workspace=Path("/workspace"),
        record={"base_commit": "0" * 40},
    )


def test_cursor_runtime_isolation_contract() -> None:
    driver = CursorDriver()
    assert CURSOR_RUNTIME_HOME == "/home/node"
    assert CURSOR_STATE_TARGET == "/home/node/.cursor"
    assert CURSOR_AUTH_TARGET == "/home/node/.config/cursor"
    assert (CURSOR_RUNTIME_UID, CURSOR_RUNTIME_GID) == (1000, 1000)
    assert CURSOR_CONTROL_ISOLATION == RuntimeIsolationRequirements(uid=1000, gid=1000)
    assert CURSOR_SANDBOX_ISOLATION == RuntimeIsolationRequirements(
        uid=1000,
        gid=1000,
        nested_sandbox_bootstrap=True,
    )

    assert runtime_isolation_args(CURSOR_CONTROL_ISOLATION) == ["--user", "1000:1000"]
    nested_args = runtime_isolation_args(CURSOR_SANDBOX_ISOLATION)
    assert nested_args == [
        "--user", "1000:1000",
        "--security-opt=unmask=/proc/*",
    ]
    assert not any(item.startswith("--cap-add") for item in nested_args)

    layouts = {layout.key: layout for layout in driver.state_adapter().volumes}
    assert layouts["state"].mount.target == CURSOR_STATE_TARGET
    assert layouts["auth"].mount.target == CURSOR_AUTH_TARGET
    for layout in layouts.values():
        assert (layout.owner_uid, layout.owner_gid) == (1000, 1000)

    auth = driver.auth_spec()
    status = driver.auth_status_spec()
    assert auth.runtime_isolation == CURSOR_CONTROL_ISOLATION
    assert status.runtime_isolation == CURSOR_CONTROL_ISOLATION

    legacy = driver.compile_policy({"readonly": False, "outer_only": False})
    sandboxed = driver.compile_policy(policy())
    unrestricted = driver.compile_policy(policy(network="allow", sandbox=False))
    assert legacy.runtime_isolation == CURSOR_CONTROL_ISOLATION
    assert unrestricted.runtime_isolation == CURSOR_CONTROL_ISOLATION
    assert sandboxed.runtime_isolation == CURSOR_SANDBOX_ISOLATION

    run_spec = driver.create_run_spec(context(), sandboxed, "inspect")
    assert run_spec.runtime_isolation == CURSOR_SANDBOX_ISOLATION
    assert run_spec.policy_artifacts.runtime_isolation == CURSOR_SANDBOX_ISOLATION

    caps = driver.capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "provider_state_protection" not in caps.policy_capabilities
    assert "hardened" not in caps.security_classes


def test_cursor_container_and_generic_runtime_boundaries() -> None:
    containerfile = (PLATFORM / "containers/Containerfile.cursor").read_text(encoding="utf-8")
    assert 'ENV HOME="/home/node"' in containerfile
    assert 'HOME=/opt/cursor-cli bash -c' in containerfile

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
    test_cursor_runtime_isolation_contract()
    test_cursor_container_and_generic_runtime_boundaries()
    print("SEC-003 Cursor runtime isolation regression checks passed")
