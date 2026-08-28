"""Provider-state metadata consumed by trusted drivers and generic runtime code.

State adapters describe persistent mounts, legacy migration, provider-native
policy-file targets, and optional managed-state reconciliation without invoking
Podman themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from agentdev.core.models import ProviderStateSpec


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise ValueError(f"{label} must be a non-empty single-line string")
    return value


def _relative_path(value: str, label: str) -> str:
    _text(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {".", ".."}:
        raise ValueError(f"{label} must be a relative path without '..'")
    return value


def _absolute_path(value: str, label: str) -> str:
    _text(value, label)
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute path without '..'")
    return value


@dataclass(frozen=True, slots=True)
class StateVolumeLayout:
    """One persistent provider volume plus migration/smoke metadata."""

    key: str
    mount: ProviderStateSpec
    staging_target: str
    marker: str
    legacy_path: str | None
    empty_error: str
    smoke_marker: str
    cleanup_after_copy: tuple[str, ...] = ()
    owner_uid: int | None = None
    owner_gid: int | None = None

    def __post_init__(self) -> None:
        _text(self.key, "state volume key")
        if not isinstance(self.mount, ProviderStateSpec):
            raise ValueError("mount must be ProviderStateSpec")
        _absolute_path(self.staging_target, "state staging target")
        _relative_path(self.marker, "state layout marker")
        if self.legacy_path is not None:
            _relative_path(self.legacy_path, "legacy state path")
        _text(self.empty_error, "empty-volume error")
        _relative_path(self.smoke_marker, "state smoke marker")
        if not isinstance(self.cleanup_after_copy, tuple):
            raise ValueError("cleanup_after_copy must be a tuple")
        for item in self.cleanup_after_copy:
            _relative_path(item, "cleanup path")
        if (self.owner_uid is None) != (self.owner_gid is None):
            raise ValueError("state owner_uid and owner_gid must be set together")
        for name in ("owner_uid", "owner_gid"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class StatePolicyMount:
    """Provider-native policy file sourced from the deployed provider seed."""

    seed_relative_path: str
    target: str
    read_only: bool = True

    def __post_init__(self) -> None:
        _relative_path(self.seed_relative_path, "policy seed path")
        _absolute_path(self.target, "policy target")
        if type(self.read_only) is not bool:
            raise ValueError("policy read_only must be boolean")


@dataclass(frozen=True, slots=True)
class JsonFieldReconciliation:
    """Keep one JSON field platform-managed in a writable provider state file."""

    volume_key: str
    seed_relative_path: str
    state_relative_path: str
    managed_field: str

    def __post_init__(self) -> None:
        _text(self.volume_key, "reconciliation volume key")
        _relative_path(self.seed_relative_path, "reconciliation seed path")
        _relative_path(self.state_relative_path, "reconciliation state path")
        _text(self.managed_field, "managed JSON field")
        if any(ch in self.managed_field for ch in ".[]'\""):
            raise ValueError("managed JSON field must be a simple object key")


@dataclass(frozen=True, slots=True)
class ProviderStateAdapter:
    """Declarative provider-state contract owned by a trusted driver."""

    volumes: tuple[StateVolumeLayout, ...]
    legacy_volume: str | None = None
    policy_mounts: tuple[StatePolicyMount, ...] = ()
    reconciliation: JsonFieldReconciliation | None = None
    primary_key: str = "state"

    def __post_init__(self) -> None:
        if not isinstance(self.volumes, tuple) or not self.volumes:
            raise ValueError("provider state adapter requires at least one volume")
        if not all(isinstance(item, StateVolumeLayout) for item in self.volumes):
            raise ValueError("volumes must contain StateVolumeLayout values")
        keys = [item.key for item in self.volumes]
        if len(keys) != len(set(keys)):
            raise ValueError("state volume keys must be unique")
        sources = [item.mount.source for item in self.volumes]
        if len(sources) != len(set(sources)):
            raise ValueError("state volume sources must be unique")
        targets = [item.mount.target for item in self.volumes]
        if len(targets) != len(set(targets)):
            raise ValueError("state volume targets must be unique")
        staging = [item.staging_target for item in self.volumes]
        if len(staging) != len(set(staging)):
            raise ValueError("state staging targets must be unique")
        if self.primary_key not in keys:
            raise ValueError("primary state key must reference a configured volume")
        if self.legacy_volume is not None:
            _text(self.legacy_volume, "legacy volume")
        if not isinstance(self.policy_mounts, tuple) or not all(
            isinstance(item, StatePolicyMount) for item in self.policy_mounts
        ):
            raise ValueError("policy_mounts must contain StatePolicyMount values")
        policy_targets = [item.target for item in self.policy_mounts]
        if len(policy_targets) != len(set(policy_targets)):
            raise ValueError("policy mount targets must be unique")
        if self.reconciliation is not None:
            if not isinstance(self.reconciliation, JsonFieldReconciliation):
                raise ValueError("reconciliation must be JsonFieldReconciliation or None")
            self.volume(self.reconciliation.volume_key)

    @classmethod
    def static(cls, mounts: tuple[ProviderStateSpec, ...]) -> "ProviderStateAdapter":
        """Create a no-migration adapter for simple/fake providers."""
        if not mounts:
            raise ValueError("static provider state requires at least one mount")
        layouts = tuple(
            StateVolumeLayout(
                key="state" if index == 0 else f"state-{index}",
                mount=mount,
                staging_target=f"/state{index}",
                marker=".agent-dev-state-layout-v1",
                legacy_path=None,
                empty_error="provider state volume is non-empty but has no layout marker",
                smoke_marker=f".agent-dev-state-{index}-write-smoke",
            )
            for index, mount in enumerate(mounts)
        )
        return cls(layouts)

    def volume(self, key: str) -> StateVolumeLayout:
        for item in self.volumes:
            if item.key == key:
                return item
        raise KeyError(key)

    def primary(self) -> StateVolumeLayout:
        return self.volume(self.primary_key)

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return tuple(item.mount for item in self.volumes)

    def writable_smoke_targets(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (item.mount.target, item.smoke_marker)
            for item in self.volumes
            if not item.mount.read_only
        )
