#!/usr/bin/env python3
"""Freeze concrete CursorDriver semantics before runtime extraction."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.base import ProviderPolicyArtifacts
from agentdev.agents.cursor import CursorDriver
from agentdev.agents.registry import BUILTIN_AGENT_REGISTRY
from agentdev.core.models import TaskContext


def context() -> TaskContext:
    return TaskContext(
        project="project",
        task="task",
        mode="integration",
        status="active",
        metadata_path=Path("/srv/agent-dev/project/tasks/task.json"),
        workspace=Path("/workspace"),
        record={"mode": "integration", "status": "active", "base_commit": "abc"},
    )


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}")


def test_identity_capabilities_and_installation() -> None:
    driver = CursorDriver()
    assert driver.id() == "cursor"
    assert driver.display_name() == "Cursor"
    caps = driver.capabilities()
    assert caps.workspace_modes == frozenset({"readonly", "writable"})
    assert caps.interactive_auth
    assert caps.interactive_run
    assert caps.native_policy
    assert caps.native_sandbox
    assert caps.compatibility_modes == frozenset()
    installation = driver.installation_spec()
    assert installation.image_key == "cursor"
    assert installation.containerfile == "Containerfile.cursor"
    assert installation.version_key is None


def test_state_and_reconciliation() -> None:
    driver = CursorDriver()
    assert tuple(item.as_dict() for item in driver.state_spec()) == (
        {"source": "agent-dev-cursor-state", "target": "/home/node/.cursor", "read_only": False},
        {"source": "agent-dev-cursor-auth", "target": "/home/node/.config/cursor", "read_only": False},
    )
    adapter = driver.state_adapter()
    assert adapter.legacy_volume == "agent-dev-cursor-home"
    assert adapter.primary().legacy_path == ".cursor"
    assert adapter.volume("auth").legacy_path == ".config/cursor"
    assert adapter.volume("auth").marker == ".agent-dev-auth-layout-v1"
    assert (adapter.primary().owner_uid, adapter.primary().owner_gid) == (1000, 1000)
    assert (adapter.volume("auth").owner_uid, adapter.volume("auth").owner_gid) == (
        1000, 1000,
    )
    reconciliation = adapter.reconciliation
    assert reconciliation is not None
    assert reconciliation.volume_key == "state"
    assert reconciliation.seed_relative_path == "cli-config.json"
    assert reconciliation.state_relative_path == "cli-config.json"
    assert reconciliation.managed_field == "permissions"


def test_auth_status_and_version() -> None:
    driver = CursorDriver()
    auth = driver.auth_spec()
    assert auth.argv == ("agent", "login")
    assert auth.environment == (("NO_OPEN_BROWSER", "1"),)
    assert auth.interactive
    assert auth.timeout_seconds == 900
    assert driver.auth_status_spec().argv == ("agent", "status")
    assert not driver.auth_status_spec().interactive
    assert driver.version_probe().argv == ("agent", "--version")


def test_run_modes() -> None:
    driver = CursorDriver()
    for readonly in (False, True):
        policy = driver.compile_policy({"readonly": readonly, "outer_only": False})
        assert isinstance(policy, ProviderPolicyArtifacts)
        assert policy.argv == ()
        spec = driver.create_run_spec(context(), policy, "")
        assert spec.argv == ("agent", "--trust")
        assert spec.interactive

    policy = driver.compile_policy({"readonly": True, "outer_only": False})
    prompted = driver.create_run_spec(context(), policy, "review this")
    assert prompted.argv == ("agent", "--trust", "review this")
    expect(ValueError, driver.compile_policy, {"readonly": False, "outer_only": True})


def test_registry_and_generic_broker_boundary() -> None:
    assert isinstance(BUILTIN_AGENT_REGISTRY.get("cursor"), CursorDriver)
    daemon = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    for forbidden in (
        'provider == "cursor"',
        '["agent", "--trust"',
        '["agent", "login"',
        '["agent", "status"',
        '("agent", "--version")',
        'NO_OPEN_BROWSER=1',
        'except NotImplementedError',
    ):
        assert forbidden not in daemon, forbidden

    source = (PLATFORM / "agentdev/agents/cursor.py").read_text(encoding="utf-8").lower()
    for forbidden in ("subprocess.", '["podman"', "socket.", "project_paths", "worktree"):
        assert forbidden not in source, forbidden


def main() -> None:
    test_identity_capabilities_and_installation()
    test_state_and_reconciliation()
    test_auth_status_and_version()
    test_run_modes()
    test_registry_and_generic_broker_boundary()
    print("Cursor driver regression checks passed")


if __name__ == "__main__":
    main()
