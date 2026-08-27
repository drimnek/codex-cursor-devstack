"""Monotonic resolution for provider-neutral execution policies.

MA2-POL-002 resolves a complete platform :class:`ExecutionPolicy` plus sparse
project, execution-profile, and run restriction layers.  Sparse layers use the
same nested field names as ``ExecutionPolicy``; omitted fields inherit the
current effective value.

Every supplied layer is normalized back through ``ExecutionPolicy`` and must be
equal to or more restrictive than the effective policy above it.  A widening
attempt is rejected rather than silently clamped.

This module does not define built-in profiles, map legacy flags, match provider
capabilities, compile provider-native configuration, or enforce runtime policy.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Mapping

from .schema import ExecutionPolicy

_ACCESS_ORDER = {"none": 0, "read": 1, "write": 2}
_REFERENCE_ORDER = {"none": 0, "read": 1}
_FILESYSTEM_ORDER = {"deny": 0, "read": 1, "write": 2}
_NETWORK_ORDER = {"deny": 0, "allowlist": 1, "allow": 2}
_VISIBILITY_ORDER = {"deny": 0, "allow": 1}
_SECURITY_ORDER = {"compatibility": 0, "hardened": 1}


class PolicyEscalationError(ValueError):
    """A lower policy layer attempted to widen an effective restriction."""

    def __init__(self, layer: str, field: str, upper: object, lower: object) -> None:
        self.layer = layer
        self.field = field
        self.upper = upper
        self.lower = lower
        super().__init__(
            f"{layer}.{field} cannot widen effective policy "
            f"from {upper!r} to {lower!r}"
        )


def _require_policy(value: object, name: str) -> ExecutionPolicy:
    if not isinstance(value, ExecutionPolicy):
        raise TypeError(f"{name} must be ExecutionPolicy")
    return value


def _require_restrictions(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{name} field names must be strings")
    return value


def _merge_restrictions(
    base: Mapping[str, object],
    restrictions: Mapping[str, object],
    *,
    layer: str,
    path: str = "",
) -> dict[str, object]:
    """Recursively overlay a sparse layer while rejecting unknown fields."""

    result = deepcopy(dict(base))
    for key, value in restrictions.items():
        field_path = f"{path}.{key}" if path else key
        if key not in base:
            raise ValueError(f"{layer}.{field_path} is an unknown policy field")
        base_value = base[key]
        if isinstance(value, Mapping) and isinstance(base_value, Mapping):
            result[key] = _merge_restrictions(
                base_value,
                _require_restrictions(value, f"{layer}.{field_path}"),
                layer=layer,
                path=field_path,
            )
        else:
            result[key] = deepcopy(value)

    # The canonical schema always serializes ``destinations``.  When a sparse
    # layer narrows an allowlist to deny, or attempts to widen it to allow,
    # omitted destinations must not be inherited into a mode where they are
    # invalid.  Entering allowlist mode still requires explicit destinations
    # unless the effective policy was already an allowlist.
    if path == "network.task_shell" and "mode" in restrictions:
        mode = restrictions["mode"]
        if mode != "allowlist" and "destinations" not in restrictions:
            result["destinations"] = []
    return result


def _candidate_from_layer(
    effective: ExecutionPolicy,
    restrictions: Mapping[str, object],
    layer: str,
) -> ExecutionPolicy:
    merged = _merge_restrictions(
        effective.as_dict(),
        restrictions,
        layer=layer,
    )
    try:
        return ExecutionPolicy.from_dict(merged)
    except ValueError as exc:
        raise ValueError(f"{layer}: {exc}") from exc


def _require_rank_not_wider(
    layer: str,
    field: str,
    upper: str,
    lower: str,
    order: dict[str, int],
) -> None:
    if order[lower] > order[upper]:
        raise PolicyEscalationError(layer, field, upper, lower)


def _require_permission_not_wider(
    layer: str,
    field: str,
    upper: bool,
    lower: bool,
) -> None:
    if lower and not upper:
        raise PolicyEscalationError(layer, field, upper, lower)


def _require_requirement_not_weaker(
    layer: str,
    field: str,
    upper: bool,
    lower: bool,
) -> None:
    if upper and not lower:
        raise PolicyEscalationError(layer, field, upper, lower)


def _require_limit_not_wider(
    layer: str,
    field: str,
    upper: int | float,
    lower: int | float,
) -> None:
    if lower > upper:
        raise PolicyEscalationError(layer, field, upper, lower)


def _validate_network_narrowing(
    layer: str,
    upper: ExecutionPolicy,
    lower: ExecutionPolicy,
) -> None:
    upper_network = upper.network.task_shell
    lower_network = lower.network.task_shell
    _require_rank_not_wider(
        layer,
        "network.task_shell.mode",
        upper_network.mode,
        lower_network.mode,
        _NETWORK_ORDER,
    )
    if upper_network.mode == lower_network.mode == "allowlist":
        upper_destinations = frozenset(upper_network.destinations)
        lower_destinations = frozenset(lower_network.destinations)
        if not lower_destinations.issubset(upper_destinations):
            raise PolicyEscalationError(
                layer,
                "network.task_shell.destinations",
                tuple(sorted(upper_destinations)),
                tuple(sorted(lower_destinations)),
            )


def _validate_narrowing(
    layer: str,
    upper: ExecutionPolicy,
    lower: ExecutionPolicy,
) -> None:
    if lower.version != upper.version:
        raise PolicyEscalationError(layer, "version", upper.version, lower.version)

    _require_rank_not_wider(
        layer,
        "workspace.access",
        upper.workspace.access,
        lower.workspace.access,
        _ACCESS_ORDER,
    )
    _require_rank_not_wider(
        layer,
        "reference.access",
        upper.reference.access,
        lower.reference.access,
        _REFERENCE_ORDER,
    )
    _require_rank_not_wider(
        layer,
        "filesystem.external",
        upper.filesystem.external,
        lower.filesystem.external,
        _FILESYSTEM_ORDER,
    )
    _validate_network_narrowing(layer, upper, lower)
    _require_rank_not_wider(
        layer,
        "credentials.provider_auth.task_shell",
        upper.credentials.provider_auth.task_shell,
        lower.credentials.provider_auth.task_shell,
        _VISIBILITY_ORDER,
    )

    _require_permission_not_wider(layer, "git.read", upper.git.read, lower.git.read)
    _require_permission_not_wider(
        layer, "git.commit", upper.git.commit, lower.git.commit
    )
    _require_permission_not_wider(layer, "git.push", upper.git.push, lower.git.push)
    _require_requirement_not_weaker(
        layer, "sandbox.required", upper.sandbox.required, lower.sandbox.required
    )

    _require_limit_not_wider(
        layer, "resources.cpu", upper.resources.cpu, lower.resources.cpu
    )
    _require_limit_not_wider(
        layer,
        "resources.memory",
        upper.resources.memory_bytes,
        lower.resources.memory_bytes,
    )
    _require_limit_not_wider(
        layer, "resources.pids", upper.resources.pids, lower.resources.pids
    )

    if _SECURITY_ORDER[lower.security_class] < _SECURITY_ORDER[upper.security_class]:
        raise PolicyEscalationError(
            layer, "security_class", upper.security_class, lower.security_class
        )


class PolicyResolver:
    """Resolve the fixed v0.2 policy hierarchy without widening restrictions."""

    def resolve(
        self,
        *,
        platform_baseline: ExecutionPolicy,
        project_policy: Mapping[str, object] | None = None,
        execution_profile: Mapping[str, object] | None = None,
        run_restrictions: Mapping[str, object] | None = None,
    ) -> ExecutionPolicy:
        effective = _require_policy(platform_baseline, "platform_baseline")
        layers = (
            ("project_policy", project_policy),
            ("execution_profile", execution_profile),
            ("run_restrictions", run_restrictions),
        )
        for layer_name, layer_value in layers:
            if layer_value is None:
                continue
            restrictions = _require_restrictions(layer_value, layer_name)
            candidate = _candidate_from_layer(effective, restrictions, layer_name)
            _validate_narrowing(layer_name, effective, candidate)
            effective = candidate
        return effective
