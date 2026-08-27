#!/usr/bin/env python3
"""Regression coverage for MA2-POL-004 capability requirement matching."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.agents.base import AgentCapabilities  # noqa: E402
from agentdev.policy.capabilities import (  # noqa: E402
    CapabilityRequirement,
    MissingCapabilitiesError,
    available_capabilities,
    require_capabilities,
    require_policy_capabilities,
)
from agentdev.policy.profiles import get_profile  # noqa: E402
from agentdev.policy.resolver import PolicyResolver  # noqa: E402
from agentdev.policy.schema import ExecutionPolicy  # noqa: E402


def baseline(*, security_class: str = "compatibility") -> ExecutionPolicy:
    return ExecutionPolicy.from_dict(
        {
            "version": 1,
            "workspace": {"access": "write"},
            "reference": {"access": "read"},
            "filesystem": {"external": "deny"},
            "network": {"task_shell": {"mode": "allow"}},
            "credentials": {"provider_auth": {"task_shell": "allow"}},
            "git": {"read": True, "commit": True, "push": False},
            "sandbox": {"required": False},
            "resources": {"cpu": 8, "memory": "16g", "pids": 2048},
            "security_class": security_class,
        }
    )


def resolved_profile(
    profile_id: str,
    *,
    security_class: str,
    destinations: list[str] | None = None,
) -> ExecutionPolicy:
    profile = get_profile(profile_id)
    restrictions = profile.restrictions(task_shell_destinations=destinations)
    return PolicyResolver().resolve(
        platform_baseline=baseline(security_class=security_class),
        execution_profile=restrictions,
    )


def check_compatibility_requirements() -> None:
    review = CapabilityRequirement.from_policy(
        resolved_profile("review", security_class="compatibility")
    )
    assert review.required == frozenset(
        {
            "workspace:readonly",
            "security_class:compatibility",
        }
    )

    implement = CapabilityRequirement.from_policy(
        resolved_profile("implement", security_class="compatibility")
    )
    assert implement.required == frozenset(
        {
            "workspace:writable",
            "security_class:compatibility",
        }
    )


def check_hardened_requirements() -> None:
    review = CapabilityRequirement.from_policy(
        resolved_profile("review", security_class="hardened")
    )
    assert review.required == frozenset(
        {
            "workspace:readonly",
            "security_class:hardened",
            "filesystem_sandbox",
            "network_deny",
            "provider_state_protection",
        }
    )

    dependency = CapabilityRequirement.from_policy(
        resolved_profile(
            "dependency",
            security_class="hardened",
            destinations=["pypi.org"],
        )
    )
    assert dependency.required == frozenset(
        {
            "workspace:writable",
            "security_class:hardened",
            "filesystem_sandbox",
            "network_allowlist",
            "provider_state_protection",
        }
    )


def check_available_capability_projection() -> None:
    caps = AgentCapabilities(
        workspace_modes=frozenset({"readonly", "writable"}),
        interactive_run=True,
        native_sandbox=True,
        compatibility_modes=frozenset({"outer-only"}),
        policy_capabilities=frozenset({"network_deny"}),
        security_classes=frozenset({"compatibility", "hardened"}),
    )
    assert available_capabilities(caps) == frozenset(
        {
            "workspace:readonly",
            "workspace:writable",
            "interactive-run",
            "compatibility:outer-only",
            "filesystem_sandbox",
            "network_deny",
            "security_class:compatibility",
            "security_class:hardened",
        }
    )


def check_precise_missing_capability() -> None:
    policy = resolved_profile("review", security_class="hardened")
    caps = AgentCapabilities(
        workspace_modes=frozenset({"readonly"}),
        native_sandbox=True,
        policy_capabilities=frozenset({"network_deny"}),
        security_classes=frozenset({"compatibility", "hardened"}),
    )
    try:
        require_policy_capabilities(policy, caps, agent_id="fake")
    except MissingCapabilitiesError as exc:
        assert exc.agent_id == "fake"
        assert exc.missing == ("provider_state_protection",)
        assert str(exc).endswith("missing capabilities: provider_state_protection")
    else:
        raise AssertionError("fake driver missing one requirement was accepted")


def check_hardened_does_not_downgrade() -> None:
    policy = resolved_profile("implement", security_class="hardened")
    caps = AgentCapabilities(
        workspace_modes=frozenset({"writable"}),
        native_sandbox=True,
        policy_capabilities=frozenset(
            {"network_deny", "provider_state_protection"}
        ),
        security_classes=frozenset({"compatibility"}),
    )
    try:
        require_policy_capabilities(policy, caps, agent_id="compat-only")
    except MissingCapabilitiesError as exc:
        assert exc.missing == ("security_class:hardened",)
        assert policy.security_class == "hardened"
    else:
        raise AssertionError("hardened request silently downgraded to compatibility")


def check_existing_plan_requirement_matching() -> None:
    caps = AgentCapabilities(
        workspace_modes=frozenset({"writable"}),
        interactive_run=True,
    )
    require_capabilities(
        frozenset({"workspace:writable", "interactive-run"}),
        caps,
        agent_id="fake",
    )
    try:
        require_capabilities(
            frozenset({"workspace:writable", "compatibility:outer-only"}),
            caps,
            agent_id="fake",
        )
    except MissingCapabilitiesError as exc:
        assert exc.missing == ("compatibility:outer-only",)
    else:
        raise AssertionError("unsupported plan compatibility mode was accepted")


def check_agent_capability_serialization_and_validation() -> None:
    caps = AgentCapabilities(
        workspace_modes=frozenset({"readonly"}),
        policy_capabilities=frozenset(
            {"network_deny", "provider_state_protection"}
        ),
        security_classes=frozenset({"hardened", "compatibility"}),
    )
    assert caps.as_dict()["policy_capabilities"] == [
        "network_deny",
        "provider_state_protection",
    ]
    assert caps.as_dict()["security_classes"] == ["compatibility", "hardened"]

    for security_classes in (frozenset(), frozenset({"unknown"})):
        try:
            AgentCapabilities(
                workspace_modes=frozenset({"readonly"}),
                security_classes=security_classes,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid security classes accepted: {security_classes}")


def check_broker_gate_precedes_runtime_execution() -> None:
    source = (PLATFORM_SRC / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    start = source.index("def op_run")
    end = source.index("def rpc_operations", start)
    op_run_source = source[start:end]
    check_position = op_run_source.index("require_capabilities(")
    runtime_position = op_run_source.index("execute_runtime_plan(")
    assert check_position < runtime_position
    assert "plan.required_capabilities" in op_run_source
    assert "driver.capabilities()" in op_run_source


def check_provider_neutral_boundary() -> None:
    source = (PLATFORM_SRC / "agentdev/policy/capabilities.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "codex",
        "cursor",
        "/root/.codex",
        "/root/.cursor",
        "podman",
        "subprocess",
    ):
        assert forbidden not in lowered, forbidden


def main() -> None:
    check_compatibility_requirements()
    check_hardened_requirements()
    check_available_capability_projection()
    check_precise_missing_capability()
    check_hardened_does_not_downgrade()
    check_existing_plan_requirement_matching()
    check_agent_capability_serialization_and_validation()
    check_broker_gate_precedes_runtime_execution()
    check_provider_neutral_boundary()
    print("capability matching regression checks passed")


if __name__ == "__main__":
    main()
