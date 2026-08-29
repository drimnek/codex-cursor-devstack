#!/usr/bin/env python3
"""Freeze concrete CodexDriver semantics before Cursor/runtime extraction."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.base import ProviderPolicyArtifacts
from agentdev.agents.codex import CodexDriver
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


def test_identity_capabilities_and_installation() -> None:
    driver = CodexDriver()
    assert driver.id() == "codex"
    assert driver.display_name() == "OpenAI Codex"
    assert driver.capabilities().workspace_modes == frozenset({"readonly", "writable"})
    assert driver.capabilities().interactive_auth
    assert driver.capabilities().interactive_run
    assert driver.capabilities().native_policy
    assert driver.capabilities().native_sandbox
    assert driver.capabilities().compatibility_modes == frozenset({"outer-only"})
    assert driver.capabilities().policy_capabilities == frozenset(
        {"provider_state_protection"}
    )
    assert driver.capabilities().security_classes == frozenset({"compatibility"})
    assert driver.installation_spec().image_key == "codex"
    assert driver.installation_spec().containerfile == "Containerfile.codex"
    assert driver.installation_spec().version_key == "codex"


def test_state_and_policy_mount() -> None:
    driver = CodexDriver()
    assert tuple(item.as_dict() for item in driver.state_spec()) == (
        {"source": "agent-dev-codex-state", "target": "/home/node/.codex", "read_only": False},
    )
    adapter = driver.state_adapter()
    assert adapter.legacy_volume == "agent-dev-codex-home"
    assert adapter.primary().legacy_path == ".codex"
    assert adapter.primary().cleanup_after_copy == ("config.toml",)
    assert tuple((item.seed_relative_path, item.target, item.read_only) for item in adapter.policy_mounts) == (
        ("config.toml", "/home/node/.codex/config.toml", True),
    )


def test_auth_status_and_version() -> None:
    driver = CodexDriver()
    auth = driver.auth_spec()
    assert auth.argv == ("codex", "login", "--device-auth")
    assert auth.environment == ()
    assert auth.interactive
    assert auth.timeout_seconds == 900
    assert driver.auth_status_spec().argv == ("codex", "login", "status")
    assert not driver.auth_status_spec().interactive
    assert driver.version_probe().argv == ("codex", "--version")


def test_run_modes() -> None:
    driver = CodexDriver()
    cases = (
        ({"readonly": False, "outer_only": False}, "workspace-write"),
        ({"readonly": True, "outer_only": False}, "read-only"),
        ({"readonly": False, "outer_only": True}, "danger-full-access"),
        ({"readonly": True, "outer_only": True}, "danger-full-access"),
    )
    for request_policy, sandbox in cases:
        policy = driver.compile_policy(request_policy)
        assert isinstance(policy, ProviderPolicyArtifacts)
        assert policy.argv == ("--sandbox", sandbox, "-c", "approval_policy=never")
        spec = driver.create_run_spec(context(), policy, "")
        assert spec.argv == (
            "codex", "exec", "--sandbox", sandbox, "-c", "approval_policy=never"
        )
        assert spec.interactive

    policy = driver.compile_policy({"readonly": True, "outer_only": False})
    prompted = driver.create_run_spec(context(), policy, "review this")
    assert prompted.argv[-1] == "review this"


def test_registry_and_generic_broker_boundary() -> None:
    assert isinstance(BUILTIN_AGENT_REGISTRY.get("codex"), CodexDriver)
    daemon = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    for forbidden in (
        'provider == "codex"',
        '["codex", "exec"',
        '["codex", "login"',
        'binary = "codex"',
    ):
        assert forbidden not in daemon, forbidden

    source = (PLATFORM / "agentdev/agents/codex.py").read_text(encoding="utf-8").lower()
    for forbidden in ("subprocess.", "[\"podman\"", "socket.", "project_paths", "worktree"):
        assert forbidden not in source, forbidden


def main() -> None:
    test_identity_capabilities_and_installation()
    test_state_and_policy_mount()
    test_auth_status_and_version()
    test_run_modes()
    test_registry_and_generic_broker_boundary()
    print("Codex driver regression checks passed")


if __name__ == "__main__":
    main()
