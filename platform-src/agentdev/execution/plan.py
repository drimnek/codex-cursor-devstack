"""Validated provider-neutral executor plans.

A resolved plan contains the complete declarative input required to start one
executor.  Provider drivers contribute command/state/native-policy semantics;
the broker resolves authorized project paths and runtime requirements before a
runtime backend receives the plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


_INTERACTION_MODES = frozenset({"interactive", "noninteractive"})
_MOUNT_ROLES = frozenset({
    "workspace",
    "reference",
    "task-metadata",
    "provider-state",
    "provider-policy",
    "git-worktree",
    "git-common",
})


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise ValueError(f"{label} must be a non-empty single-line string")
    return value


def _require_container_path(value: str, label: str) -> str:
    _require_text(value, label)
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute container path without '..'")
    return value


def _require_argv(argv: tuple[str, ...]) -> None:
    if not isinstance(argv, tuple) or not argv:
        raise ValueError("plan argv must be a non-empty tuple")
    for value in argv:
        _require_text(value, "plan argv item")


def _require_environment(environment: tuple[tuple[str, str], ...]) -> None:
    if not isinstance(environment, tuple):
        raise ValueError("plan environment must be a tuple")
    seen: set[str] = set()
    for item in environment:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("plan environment items must be (name, value) tuples")
        name, value = item
        _require_text(name, "plan environment name")
        if "=" in name:
            raise ValueError("plan environment name must not contain '='")
        if name in seen:
            raise ValueError(f"plan environment contains duplicate key {name!r}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("plan environment value must be a string without NUL")
        seen.add(name)


@dataclass(frozen=True, slots=True)
class ExecutionMount:
    """One already-authorized runtime mount."""

    source: str
    target: str
    read_only: bool
    role: str

    def __post_init__(self) -> None:
        _require_text(self.source, "mount source")
        _require_container_path(self.target, "mount target")
        if type(self.read_only) is not bool:
            raise ValueError("mount read_only must be boolean")
        if self.role not in _MOUNT_ROLES:
            raise ValueError(f"unsupported mount role {self.role!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "read_only": self.read_only,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Runtime resource limits already resolved by the broker."""

    pids: int
    memory: str
    cpus: int | float

    def __post_init__(self) -> None:
        if type(self.pids) is not int or self.pids <= 0:
            raise ValueError("pids must be a positive integer")
        _require_text(self.memory, "memory limit")
        if isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float)) or self.cpus <= 0:
            raise ValueError("cpus must be a positive number")

    def as_dict(self) -> dict[str, Any]:
        return {"pids": self.pids, "memory": self.memory, "cpus": self.cpus}


@dataclass(frozen=True, slots=True)
class NetworkRuntimeRequirements:
    """Concrete network runtime requirements, prior to backend translation."""

    mode: str
    http_proxy: bool = False

    def __post_init__(self) -> None:
        _require_text(self.mode, "network mode")
        if type(self.http_proxy) is not bool:
            raise ValueError("http_proxy must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "http_proxy": self.http_proxy}


@dataclass(frozen=True, slots=True)
class ResolvedProviderPolicyArtifacts:
    """Provider-native policy output retained in the resolved plan."""

    mounts: tuple[ExecutionMount, ...] = ()
    argv: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mounts, tuple) or not all(
            isinstance(item, ExecutionMount) and item.role == "provider-policy"
            for item in self.mounts
        ):
            raise ValueError("policy mounts must be provider-policy ExecutionMount values")
        if not isinstance(self.argv, tuple):
            raise ValueError("policy argv must be a tuple")
        for value in self.argv:
            _require_text(value, "policy argv item")
        _require_environment(self.environment)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mounts": [item.as_dict() for item in self.mounts],
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
        }


@dataclass(frozen=True, slots=True)
class ResolvedExecutionPlan:
    """Complete validated input for one runtime execution."""

    agent_id: str
    image: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    workspace_mount: ExecutionMount
    reference_mounts: tuple[ExecutionMount, ...]
    task_metadata_mount: ExecutionMount
    provider_state_mounts: tuple[ExecutionMount, ...]
    provider_policy_artifacts: ResolvedProviderPolicyArtifacts
    resource_limits: ResourceLimits
    network: NetworkRuntimeRequirements
    readonly: bool
    interaction_mode: str
    security_class: str | None = None
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    auxiliary_mounts: tuple[ExecutionMount, ...] = ()
    working_directory: str = "/workspace"

    def __post_init__(self) -> None:
        _require_text(self.agent_id, "agent_id")
        _require_text(self.image, "image")
        _require_argv(self.argv)
        _require_environment(self.environment)
        if not isinstance(self.workspace_mount, ExecutionMount) or self.workspace_mount.role != "workspace":
            raise ValueError("workspace_mount must have role 'workspace'")
        if self.workspace_mount.target != "/workspace":
            raise ValueError("workspace mount target must be /workspace")
        if type(self.readonly) is not bool:
            raise ValueError("readonly must be boolean")
        if self.workspace_mount.read_only != self.readonly:
            raise ValueError("workspace mount mode contradicts plan readonly mode")
        if not isinstance(self.reference_mounts, tuple) or not all(
            isinstance(item, ExecutionMount) and item.role == "reference" and item.read_only
            for item in self.reference_mounts
        ):
            raise ValueError("reference mounts must be read-only reference mounts")
        if not isinstance(self.task_metadata_mount, ExecutionMount) or (
            self.task_metadata_mount.role != "task-metadata" or not self.task_metadata_mount.read_only
        ):
            raise ValueError("task metadata mount must be read-only")
        if not isinstance(self.provider_state_mounts, tuple) or not all(
            isinstance(item, ExecutionMount) and item.role == "provider-state"
            for item in self.provider_state_mounts
        ):
            raise ValueError("provider state mounts must have role 'provider-state'")
        if not isinstance(self.provider_policy_artifacts, ResolvedProviderPolicyArtifacts):
            raise ValueError("provider_policy_artifacts must be ResolvedProviderPolicyArtifacts")
        if not isinstance(self.resource_limits, ResourceLimits):
            raise ValueError("resource_limits must be ResourceLimits")
        if not isinstance(self.network, NetworkRuntimeRequirements):
            raise ValueError("network must be NetworkRuntimeRequirements")
        if self.interaction_mode not in _INTERACTION_MODES:
            raise ValueError(f"unsupported interaction mode {self.interaction_mode!r}")
        if self.security_class is not None:
            _require_text(self.security_class, "security_class")
        if not isinstance(self.required_capabilities, frozenset):
            raise ValueError("required_capabilities must be a frozenset")
        for capability in self.required_capabilities:
            _require_text(capability, "required capability")
        if not isinstance(self.auxiliary_mounts, tuple) or not all(
            isinstance(item, ExecutionMount) for item in self.auxiliary_mounts
        ):
            raise ValueError("auxiliary_mounts must be ExecutionMount values")
        _require_container_path(self.working_directory, "working_directory")

        mounts = (
            (self.workspace_mount,)
            + self.reference_mounts
            + (self.task_metadata_mount,)
            + self.provider_state_mounts
            + self.provider_policy_artifacts.mounts
            + self.auxiliary_mounts
        )
        targets: dict[str, ExecutionMount] = {}
        for mount in mounts:
            previous = targets.get(mount.target)
            if previous is not None and previous != mount:
                raise ValueError(f"contradictory mounts target {mount.target!r}")
            targets[mount.target] = mount

    def all_mounts(self) -> tuple[ExecutionMount, ...]:
        return (
            (self.workspace_mount,)
            + self.reference_mounts
            + (self.task_metadata_mount,)
            + self.provider_state_mounts
            + self.provider_policy_artifacts.mounts
            + self.auxiliary_mounts
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "image": self.image,
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
            "workspace_mount": self.workspace_mount.as_dict(),
            "reference_mounts": [item.as_dict() for item in self.reference_mounts],
            "task_metadata_mount": self.task_metadata_mount.as_dict(),
            "provider_state_mounts": [item.as_dict() for item in self.provider_state_mounts],
            "provider_policy_artifacts": self.provider_policy_artifacts.as_dict(),
            "resource_limits": self.resource_limits.as_dict(),
            "network": self.network.as_dict(),
            "readonly": self.readonly,
            "interaction_mode": self.interaction_mode,
            "security_class": self.security_class,
            "required_capabilities": sorted(self.required_capabilities),
            "auxiliary_mounts": [item.as_dict() for item in self.auxiliary_mounts],
            "working_directory": self.working_directory,
        }
