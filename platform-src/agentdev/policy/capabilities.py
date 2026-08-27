"""Provider-neutral capability requirements and fail-closed matching.

MA2-POL-004 derives the provider capabilities required by one resolved
``ExecutionPolicy`` and validates those requirements against ``AgentCapabilities``.
The matcher also accepts the already-existing ``ResolvedExecutionPlan``
capability strings so every current broker-managed agent run is checked before
runtime execution.

This module does not map legacy run flags to profiles, compile provider-native
policy, or certify hardened capabilities.  Hardened support remains evidence-
gated by the later security acceptance work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agentdev.agents.base import AgentCapabilities

from .schema import ExecutionPolicy


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Deterministic set of capabilities required by one resolved policy."""

    required: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.required, frozenset):
            raise ValueError("required capabilities must be a frozenset")
        for capability in self.required:
            _require_capability_name(capability)

    @classmethod
    def from_policy(cls, policy: ExecutionPolicy) -> "CapabilityRequirement":
        if not isinstance(policy, ExecutionPolicy):
            raise TypeError("policy must be ExecutionPolicy")

        required: set[str] = {f"security_class:{policy.security_class}"}
        if policy.workspace.access == "read":
            required.add("workspace:readonly")
        elif policy.workspace.access == "write":
            required.add("workspace:writable")

        # Compatibility deliberately does not claim provider-native sandbox,
        # credential confidentiality, or task-shell egress guarantees.  Those
        # capabilities become mandatory only for hardened execution.
        if policy.security_class == "hardened":
            if policy.sandbox.required:
                required.add("filesystem_sandbox")
            if policy.network.task_shell.mode == "deny":
                required.add("network_deny")
            elif policy.network.task_shell.mode == "allowlist":
                required.add("network_allowlist")
            if policy.credentials.provider_auth.task_shell == "deny":
                required.add("provider_state_protection")

        return cls(frozenset(required))

    def missing_from(self, capabilities: AgentCapabilities) -> tuple[str, ...]:
        available = available_capabilities(capabilities)
        return tuple(sorted(self.required - available))

    def as_dict(self) -> dict[str, list[str]]:
        return {"required": sorted(self.required)}


class MissingCapabilitiesError(ValueError):
    """Raised when an agent cannot satisfy all required capabilities."""

    def __init__(self, missing: Iterable[str], *, agent_id: str | None = None) -> None:
        normalized = tuple(sorted({_require_capability_name(item) for item in missing}))
        if not normalized:
            raise ValueError("missing capability list must not be empty")
        if agent_id is not None:
            _require_capability_name(agent_id)
        self.missing = normalized
        self.agent_id = agent_id
        prefix = f"agent {agent_id!r} cannot satisfy execution policy" if agent_id else "agent cannot satisfy execution policy"
        super().__init__(f"{prefix}; missing capabilities: {', '.join(normalized)}")


def _require_capability_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or "\n" in value
    ):
        raise ValueError("capability names must be non-empty single-line strings without surrounding whitespace")
    return value


def available_capabilities(capabilities: AgentCapabilities) -> frozenset[str]:
    """Flatten ``AgentCapabilities`` into the names used by the matcher."""

    if not isinstance(capabilities, AgentCapabilities):
        raise TypeError("capabilities must be AgentCapabilities")

    available: set[str] = set(capabilities.policy_capabilities)
    available.update(f"workspace:{mode}" for mode in capabilities.workspace_modes)
    available.update(f"security_class:{name}" for name in capabilities.security_classes)
    available.update(
        f"compatibility:{mode}" for mode in capabilities.compatibility_modes
    )
    if capabilities.interactive_run:
        available.add("interactive-run")
    if capabilities.native_sandbox:
        available.add("filesystem_sandbox")
    return frozenset(available)


def require_capabilities(
    required: CapabilityRequirement | Iterable[str],
    capabilities: AgentCapabilities,
    *,
    agent_id: str | None = None,
) -> None:
    """Fail closed when ``capabilities`` does not cover ``required``."""

    if isinstance(required, CapabilityRequirement):
        required_set = required.required
    else:
        try:
            required_set = frozenset(_require_capability_name(item) for item in required)
        except TypeError as exc:
            raise TypeError("required capabilities must be iterable") from exc

    missing = required_set - available_capabilities(capabilities)
    if missing:
        raise MissingCapabilitiesError(missing, agent_id=agent_id)


def require_policy_capabilities(
    policy: ExecutionPolicy,
    capabilities: AgentCapabilities,
    *,
    agent_id: str | None = None,
) -> CapabilityRequirement:
    """Derive and validate the requirements for one resolved policy."""

    requirement = CapabilityRequirement.from_policy(policy)
    require_capabilities(requirement, capabilities, agent_id=agent_id)
    return requirement
