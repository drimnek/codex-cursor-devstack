#!/usr/bin/env python3
"""Regression checks for MA2-POL-007 Cursor policy compilation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.cursor import CursorDriver, UnsupportedCursorPolicyError
from agentdev.core.models import TaskContext
from agentdev.policy.schema import ExecutionPolicy


def policy(
    *,
    workspace="write",
    network="deny",
    sandbox=True,
    security_class="compatibility",
    provider_auth="deny",
):
    destinations = []
    if network == "allowlist":
        destinations = ["pypi.org", "registry.npmjs.org"]
    return ExecutionPolicy.from_dict({
        "version": 1,
        "workspace": {"access": workspace},
        "reference": {"access": "read"},
        "filesystem": {"external": "deny"},
        "network": {"task_shell": {"mode": network, "destinations": destinations}},
        "credentials": {"provider_auth": {"task_shell": provider_auth}},
        "git": {"read": True, "commit": workspace == "write", "push": False},
        "sandbox": {"required": sandbox},
        "resources": {"cpu": 4, "memory": "8g", "pids": 1024},
        "security_class": security_class,
    })


def context() -> TaskContext:
    return TaskContext(
        project="demo",
        task="REQ-1",
        mode="integration",
        status="active",
        metadata_path=Path("/srv/agent-dev/projects/demo/tasks/REQ-1.json"),
        workspace=Path("/workspace"),
        record={"base_commit": "0" * 40},
    )


def expect_message(fragment: str, fn, *args) -> None:
    try:
        fn(*args)
    except UnsupportedCursorPolicyError as exc:
        assert fragment in str(exc), (fragment, str(exc))
        return
    raise AssertionError(f"expected UnsupportedCursorPolicyError containing {fragment!r}")


def test_native_sandbox_translation() -> None:
    driver = CursorDriver()
    assert driver.capabilities().native_sandbox
    assert driver.capabilities().security_classes == frozenset({"compatibility"})
    assert "network_deny" not in driver.capabilities().policy_capabilities
    assert "network_allowlist" not in driver.capabilities().policy_capabilities
    assert "provider_state_protection" not in driver.capabilities().policy_capabilities

    for workspace in ("read", "write"):
        compiled = driver.compile_policy(policy(workspace=workspace, network="deny"))
        assert compiled.argv == ("--sandbox", "enabled")
        spec = driver.create_run_spec(context(), compiled, "inspect")
        assert spec.argv == ("agent", "--trust", "--sandbox", "enabled", "inspect")

    unrestricted = driver.compile_policy(
        policy(network="allow", sandbox=False, provider_auth="allow")
    )
    assert unrestricted.argv == ("--sandbox", "disabled")


def test_fail_closed_policy_limits() -> None:
    driver = CursorDriver()
    expect_message(
        "destination allowlists are deferred",
        driver.compile_policy,
        policy(network="allowlist"),
    )
    expect_message(
        "sandbox.required=true",
        driver.compile_policy,
        policy(network="allow", sandbox=True),
    )
    expect_message(
        "workspace.access=none",
        driver.compile_policy,
        policy(workspace="none"),
    )
    expect_message(
        "security_class:hardened",
        driver.compile_policy,
        policy(security_class="hardened"),
    )
    expect_message(
        "provider-auth task-shell deny requires provider-native sandboxing",
        driver.compile_policy,
        policy(network="allow", sandbox=False),
    )


def test_legacy_path_and_reconciliation_boundary() -> None:
    driver = CursorDriver()
    for readonly in (False, True):
        compiled = driver.compile_policy({"readonly": readonly, "outer_only": False})
        assert compiled.argv == ()
        assert driver.create_run_spec(context(), compiled, "").argv == ("agent", "--trust")

    try:
        driver.compile_policy({"readonly": False, "outer_only": True})
    except ValueError as exc:
        assert str(exc) == "Cursor does not support outer-only mode"
    else:
        raise AssertionError("legacy outer-only Cursor run was accepted")

    reconciliation = driver.state_adapter().reconciliation
    assert reconciliation is not None
    assert reconciliation.managed_field == "permissions"
    assert reconciliation.state_relative_path == "cli-config.json"


def test_provider_native_strings_stay_in_driver() -> None:
    generic_policy = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PLATFORM / "agentdev/policy").glob("*.py")
    )
    for native in ("--sandbox", "Cursor does not support outer-only mode"):
        assert native not in generic_policy


def main() -> None:
    test_native_sandbox_translation()
    test_fail_closed_policy_limits()
    test_legacy_path_and_reconciliation_boundary()
    test_provider_native_strings_stay_in_driver()
    print("Cursor policy compiler regression checks passed")


if __name__ == "__main__":
    main()
