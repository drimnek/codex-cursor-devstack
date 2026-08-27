"""Strict provider-neutral execution-policy schema.

This module defines policy intent only.  It does not resolve policy layers,
select execution profiles, match provider capabilities, compile native provider
configuration, or enforce runtime behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Sequence

_POLICY_VERSION = 1
_WORKSPACE_ACCESS = frozenset({"none", "read", "write"})
_REFERENCE_ACCESS = frozenset({"none", "read"})
_EXTERNAL_FILESYSTEM_ACCESS = frozenset({"deny", "read", "write"})
_TASK_SHELL_NETWORK_MODES = frozenset({"deny", "allowlist", "allow"})
_CREDENTIAL_VISIBILITY = frozenset({"deny", "allow"})
_SECURITY_CLASSES = frozenset({"hardened", "compatibility"})
_MEMORY_RE = re.compile(r"([1-9][0-9]*)([kKmMgGtT]?)\Z")
_MEMORY_MULTIPLIERS = {
    "": 1,
    "k": 1024,
    "m": 1024**2,
    "g": 1024**3,
    "t": 1024**4,
}


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{path} field names must be strings")
    return value


def _require_fields(
    value: object,
    path: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, object]:
    mapping = _require_mapping(value, path)
    keys = frozenset(mapping)
    unknown = sorted(keys - required - optional)
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {', '.join(unknown)}")
    missing = sorted(required - keys)
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")
    return mapping


def _require_choice(value: object, path: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{path} must be one of: {choices}")
    return value


def _require_bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{path} must be boolean")
    return value


def _require_positive_int(value: object, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _normalize_cpu(value: object) -> int | float:
    if type(value) is int:
        if value <= 0:
            raise ValueError("resources.cpu must be a positive number")
        return value
    if isinstance(value, bool) or not isinstance(value, float):
        raise ValueError("resources.cpu must be a positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("resources.cpu must be a positive finite number")
    if value.is_integer():
        return int(value)
    return value


def _normalize_memory(value: object) -> int:
    if type(value) is int:
        if value <= 0:
            raise ValueError("resources.memory must be positive")
        return value
    if not isinstance(value, str):
        raise ValueError(
            "resources.memory must be positive integer bytes or an integer k/m/g/t quantity"
        )
    match = _MEMORY_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            "resources.memory must be positive integer bytes or an integer k/m/g/t quantity"
        )
    quantity = int(match.group(1))
    suffix = match.group(2).lower()
    return quantity * _MEMORY_MULTIPLIERS[suffix]


def _normalize_destinations(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("network.task_shell.destinations must be a list or tuple")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or "\x00" in item
            or "\n" in item
        ):
            raise ValueError(
                "network.task_shell destinations must be non-empty single-line strings without surrounding whitespace"
            )
        if item in seen:
            raise ValueError(f"network.task_shell contains duplicate destination {item!r}")
        seen.add(item)
        result.append(item)
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    access: str

    def __post_init__(self) -> None:
        _require_choice(self.access, "workspace.access", _WORKSPACE_ACCESS)

    @classmethod
    def from_dict(cls, value: object) -> "WorkspacePolicy":
        data = _require_fields(value, "workspace", required=frozenset({"access"}))
        return cls(_require_choice(data["access"], "workspace.access", _WORKSPACE_ACCESS))

    def as_dict(self) -> dict[str, Any]:
        return {"access": self.access}


@dataclass(frozen=True, slots=True)
class ReferencePolicy:
    access: str

    def __post_init__(self) -> None:
        _require_choice(self.access, "reference.access", _REFERENCE_ACCESS)

    @classmethod
    def from_dict(cls, value: object) -> "ReferencePolicy":
        data = _require_fields(value, "reference", required=frozenset({"access"}))
        return cls(_require_choice(data["access"], "reference.access", _REFERENCE_ACCESS))

    def as_dict(self) -> dict[str, Any]:
        return {"access": self.access}


@dataclass(frozen=True, slots=True)
class FilesystemPolicy:
    external: str

    def __post_init__(self) -> None:
        _require_choice(
            self.external, "filesystem.external", _EXTERNAL_FILESYSTEM_ACCESS
        )

    @classmethod
    def from_dict(cls, value: object) -> "FilesystemPolicy":
        data = _require_fields(value, "filesystem", required=frozenset({"external"}))
        return cls(
            _require_choice(
                data["external"], "filesystem.external", _EXTERNAL_FILESYSTEM_ACCESS
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {"external": self.external}


@dataclass(frozen=True, slots=True)
class TaskShellNetworkPolicy:
    mode: str
    destinations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_choice(self.mode, "network.task_shell.mode", _TASK_SHELL_NETWORK_MODES)
        if not isinstance(self.destinations, tuple):
            raise ValueError("network.task_shell.destinations must be a tuple")
        normalized = _normalize_destinations(self.destinations)
        object.__setattr__(self, "destinations", normalized)
        if self.mode == "allowlist" and not self.destinations:
            raise ValueError("network.task_shell allowlist mode requires destinations")
        if self.mode != "allowlist" and self.destinations:
            raise ValueError(
                "network.task_shell destinations are only valid in allowlist mode"
            )

    @classmethod
    def from_dict(cls, value: object) -> "TaskShellNetworkPolicy":
        data = _require_fields(
            value,
            "network.task_shell",
            required=frozenset({"mode"}),
            optional=frozenset({"destinations"}),
        )
        mode = _require_choice(
            data["mode"], "network.task_shell.mode", _TASK_SHELL_NETWORK_MODES
        )
        destinations = _normalize_destinations(data.get("destinations", ()))
        return cls(mode=mode, destinations=destinations)

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "destinations": list(self.destinations)}


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    task_shell: TaskShellNetworkPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.task_shell, TaskShellNetworkPolicy):
            raise ValueError("network.task_shell must be TaskShellNetworkPolicy")

    @classmethod
    def from_dict(cls, value: object) -> "NetworkPolicy":
        data = _require_fields(value, "network", required=frozenset({"task_shell"}))
        return cls(TaskShellNetworkPolicy.from_dict(data["task_shell"]))

    def as_dict(self) -> dict[str, Any]:
        return {"task_shell": self.task_shell.as_dict()}


@dataclass(frozen=True, slots=True)
class ProviderAuthPolicy:
    task_shell: str

    def __post_init__(self) -> None:
        _require_choice(
            self.task_shell,
            "credentials.provider_auth.task_shell",
            _CREDENTIAL_VISIBILITY,
        )

    @classmethod
    def from_dict(cls, value: object) -> "ProviderAuthPolicy":
        data = _require_fields(
            value, "credentials.provider_auth", required=frozenset({"task_shell"})
        )
        return cls(
            _require_choice(
                data["task_shell"],
                "credentials.provider_auth.task_shell",
                _CREDENTIAL_VISIBILITY,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {"task_shell": self.task_shell}


@dataclass(frozen=True, slots=True)
class CredentialsPolicy:
    provider_auth: ProviderAuthPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.provider_auth, ProviderAuthPolicy):
            raise ValueError("credentials.provider_auth must be ProviderAuthPolicy")

    @classmethod
    def from_dict(cls, value: object) -> "CredentialsPolicy":
        data = _require_fields(
            value, "credentials", required=frozenset({"provider_auth"})
        )
        return cls(ProviderAuthPolicy.from_dict(data["provider_auth"]))

    def as_dict(self) -> dict[str, Any]:
        return {"provider_auth": self.provider_auth.as_dict()}


@dataclass(frozen=True, slots=True)
class GitPolicy:
    read: bool
    commit: bool
    push: bool

    def __post_init__(self) -> None:
        _require_bool(self.read, "git.read")
        _require_bool(self.commit, "git.commit")
        _require_bool(self.push, "git.push")

    @classmethod
    def from_dict(cls, value: object) -> "GitPolicy":
        data = _require_fields(
            value, "git", required=frozenset({"read", "commit", "push"})
        )
        return cls(
            read=_require_bool(data["read"], "git.read"),
            commit=_require_bool(data["commit"], "git.commit"),
            push=_require_bool(data["push"], "git.push"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"read": self.read, "commit": self.commit, "push": self.push}


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    required: bool

    def __post_init__(self) -> None:
        _require_bool(self.required, "sandbox.required")

    @classmethod
    def from_dict(cls, value: object) -> "SandboxPolicy":
        data = _require_fields(value, "sandbox", required=frozenset({"required"}))
        return cls(_require_bool(data["required"], "sandbox.required"))

    def as_dict(self) -> dict[str, Any]:
        return {"required": self.required}


@dataclass(frozen=True, slots=True)
class PolicyResourceLimits:
    cpu: int | float
    memory_bytes: int
    pids: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cpu", _normalize_cpu(self.cpu))
        _require_positive_int(self.memory_bytes, "resources.memory_bytes")
        _require_positive_int(self.pids, "resources.pids")

    @classmethod
    def from_dict(cls, value: object) -> "PolicyResourceLimits":
        data = _require_fields(
            value,
            "resources",
            required=frozenset({"cpu", "memory", "pids"}),
        )
        return cls(
            cpu=_normalize_cpu(data["cpu"]),
            memory_bytes=_normalize_memory(data["memory"]),
            pids=_require_positive_int(data["pids"], "resources.pids"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"cpu": self.cpu, "memory": self.memory_bytes, "pids": self.pids}


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """One complete provider-neutral execution policy."""

    version: int
    workspace: WorkspacePolicy
    reference: ReferencePolicy
    filesystem: FilesystemPolicy
    network: NetworkPolicy
    credentials: CredentialsPolicy
    git: GitPolicy
    sandbox: SandboxPolicy
    resources: PolicyResourceLimits
    security_class: str

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != _POLICY_VERSION:
            raise ValueError(f"version must be {_POLICY_VERSION}")
        for path, value, expected in (
            ("workspace", self.workspace, WorkspacePolicy),
            ("reference", self.reference, ReferencePolicy),
            ("filesystem", self.filesystem, FilesystemPolicy),
            ("network", self.network, NetworkPolicy),
            ("credentials", self.credentials, CredentialsPolicy),
            ("git", self.git, GitPolicy),
            ("sandbox", self.sandbox, SandboxPolicy),
            ("resources", self.resources, PolicyResourceLimits),
        ):
            if not isinstance(value, expected):
                raise ValueError(f"{path} must be {expected.__name__}")
        _require_choice(self.security_class, "security_class", _SECURITY_CLASSES)

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionPolicy":
        data = _require_fields(
            value,
            "policy",
            required=frozenset(
                {
                    "version",
                    "workspace",
                    "reference",
                    "filesystem",
                    "network",
                    "credentials",
                    "git",
                    "sandbox",
                    "resources",
                    "security_class",
                }
            ),
        )
        version = data["version"]
        if type(version) is not int or version != _POLICY_VERSION:
            raise ValueError(f"version must be {_POLICY_VERSION}")
        return cls(
            version=version,
            workspace=WorkspacePolicy.from_dict(data["workspace"]),
            reference=ReferencePolicy.from_dict(data["reference"]),
            filesystem=FilesystemPolicy.from_dict(data["filesystem"]),
            network=NetworkPolicy.from_dict(data["network"]),
            credentials=CredentialsPolicy.from_dict(data["credentials"]),
            git=GitPolicy.from_dict(data["git"]),
            sandbox=SandboxPolicy.from_dict(data["sandbox"]),
            resources=PolicyResourceLimits.from_dict(data["resources"]),
            security_class=_require_choice(
                data["security_class"], "security_class", _SECURITY_CLASSES
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspace": self.workspace.as_dict(),
            "reference": self.reference.as_dict(),
            "filesystem": self.filesystem.as_dict(),
            "network": self.network.as_dict(),
            "credentials": self.credentials.as_dict(),
            "git": self.git.as_dict(),
            "sandbox": self.sandbox.as_dict(),
            "resources": self.resources.as_dict(),
            "security_class": self.security_class,
        }
