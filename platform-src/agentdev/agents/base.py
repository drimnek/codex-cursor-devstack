"""Provider integration contracts for coding-agent drivers.

The objects in this module are declarative.  Drivers describe provider
semantics; they do not start containers, select host mounts, or own project and
task lifecycle state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from agentdev.agents.state import ProviderStateAdapter
from agentdev.core.models import ProviderStateSpec, TaskContext


_WORKSPACE_MODES = frozenset({"readonly", "writable"})


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise ValueError(f"{label} must be a non-empty single-line string")
    return value


def _require_argv(argv: tuple[str, ...], label: str) -> None:
    if not isinstance(argv, tuple) or not argv:
        raise ValueError(f"{label} argv must be a non-empty tuple")
    for value in argv:
        _require_text(value, f"{label} argv item")


def _require_environment(environment: tuple[tuple[str, str], ...], label: str) -> None:
    if not isinstance(environment, tuple):
        raise ValueError(f"{label} environment must be a tuple")
    seen: set[str] = set()
    for item in environment:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{label} environment items must be (name, value) tuples")
        name, value = item
        _require_text(name, f"{label} environment name")
        if "=" in name:
            raise ValueError(f"{label} environment name must not contain '='")
        if name in seen:
            raise ValueError(f"{label} environment contains duplicate key {name!r}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{label} environment value must be a string without NUL")
        seen.add(name)


def _require_container_target(value: str, label: str) -> str:
    _require_text(value, label)
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute container path without '..'")
    return value


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Provider features known before policy/capability matching is introduced."""

    workspace_modes: frozenset[str]
    interactive_auth: bool = False
    interactive_run: bool = False
    native_policy: bool = False
    native_sandbox: bool = False
    compatibility_modes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_modes, frozenset):
            raise ValueError("workspace_modes must be a frozenset")
        unknown_modes = self.workspace_modes - _WORKSPACE_MODES
        if unknown_modes:
            raise ValueError(f"unsupported workspace modes: {sorted(unknown_modes)!r}")
        if not self.workspace_modes:
            raise ValueError("at least one workspace mode is required")
        for name in ("interactive_auth", "interactive_run", "native_policy", "native_sandbox"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.compatibility_modes, frozenset):
            raise ValueError("compatibility_modes must be a frozenset")
        for mode in self.compatibility_modes:
            _require_text(mode, "compatibility mode")

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_modes": sorted(self.workspace_modes),
            "interactive_auth": self.interactive_auth,
            "interactive_run": self.interactive_run,
            "native_policy": self.native_policy,
            "native_sandbox": self.native_sandbox,
            "compatibility_modes": sorted(self.compatibility_modes),
        }


@dataclass(frozen=True, slots=True)
class InstallationSpec:
    """Trusted in-tree build metadata for one provider image."""

    image_key: str
    containerfile: str
    version_key: str | None = None
    build_arguments: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.image_key, "image_key")
        _require_text(self.containerfile, "containerfile")
        if self.version_key is not None:
            _require_text(self.version_key, "version_key")
        _require_environment(self.build_arguments, "build arguments")

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_key": self.image_key,
            "containerfile": self.containerfile,
            "version_key": self.version_key,
            "build_arguments": [list(item) for item in self.build_arguments],
        }


@dataclass(frozen=True, slots=True)
class PolicyFileSpec:
    """One provider-native policy file exposed at a container path."""

    source: str
    target: str
    read_only: bool = True

    def __post_init__(self) -> None:
        _require_text(self.source, "policy source")
        _require_container_target(self.target, "policy target")
        if type(self.read_only) is not bool:
            raise ValueError("read_only must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "read_only": self.read_only}


@dataclass(frozen=True, slots=True)
class ProviderPolicyArtifacts:
    """Provider-native output produced by policy compilation."""

    files: tuple[PolicyFileSpec, ...] = ()
    argv: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.files, tuple) or not all(isinstance(item, PolicyFileSpec) for item in self.files):
            raise ValueError("files must be a tuple of PolicyFileSpec values")
        targets = [item.target for item in self.files]
        if len(targets) != len(set(targets)):
            raise ValueError("policy file targets must be unique")
        if not isinstance(self.argv, tuple):
            raise ValueError("policy argv must be a tuple")
        for item in self.argv:
            _require_text(item, "policy argv item")
        _require_environment(self.environment, "policy")

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [item.as_dict() for item in self.files],
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
        }


@dataclass(frozen=True, slots=True)
class AuthSpec:
    """Declarative authentication invocation."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    interactive: bool = True
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        _require_argv(self.argv, "auth")
        _require_environment(self.environment, "auth")
        if type(self.interactive) is not bool:
            raise ValueError("interactive must be boolean")
        if self.timeout_seconds is not None and (
            type(self.timeout_seconds) is not int or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive integer or None")

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
            "interactive": self.interactive,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class VersionProbeSpec:
    """Declarative provider CLI version probe."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_argv(self.argv, "version probe")
        _require_environment(self.environment, "version probe")

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
        }


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Provider command description consumed later by execution/runtime layers."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    interactive: bool = True
    policy_artifacts: ProviderPolicyArtifacts = field(default_factory=ProviderPolicyArtifacts)

    def __post_init__(self) -> None:
        _require_argv(self.argv, "run")
        _require_environment(self.environment, "run")
        if type(self.interactive) is not bool:
            raise ValueError("interactive must be boolean")
        if not isinstance(self.policy_artifacts, ProviderPolicyArtifacts):
            raise ValueError("policy_artifacts must be ProviderPolicyArtifacts")

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "environment": [list(item) for item in self.environment],
            "interactive": self.interactive,
            "policy_artifacts": self.policy_artifacts.as_dict(),
        }


class AgentDriver(ABC):
    """Minimal provider contract.  Implementations are declarative only."""

    @abstractmethod
    def id(self) -> str:
        """Return the stable registry identifier."""

    @abstractmethod
    def display_name(self) -> str:
        """Return a human-readable provider name."""

    @abstractmethod
    def capabilities(self) -> AgentCapabilities:
        """Return provider capabilities without probing mutable runtime state."""

    @abstractmethod
    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        """Return persistent provider-state mounts required by this driver."""

    def state_adapter(self) -> ProviderStateAdapter:
        """Return provider-state metadata; simple drivers get a static adapter."""
        return ProviderStateAdapter.static(self.state_spec())

    @abstractmethod
    def installation_spec(self) -> InstallationSpec:
        """Return trusted image/build metadata for this driver."""

    @abstractmethod
    def version_probe(self) -> VersionProbeSpec:
        """Return the provider CLI version command."""

    @abstractmethod
    def auth_spec(self) -> AuthSpec:
        """Return the provider authentication invocation."""

    @abstractmethod
    def auth_status_spec(self) -> RunSpec:
        """Return the provider authentication-status invocation."""

    @abstractmethod
    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        """Translate broker-owned policy into provider-native artifacts."""

    @abstractmethod
    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        """Build a provider command without invoking the runtime backend."""
