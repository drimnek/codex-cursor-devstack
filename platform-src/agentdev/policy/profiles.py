"""Trusted built-in execution profiles for provider-neutral policy resolution.

MA2-POL-003 defines the four v0.2 execution profiles as sparse policy layers.
The profiles contain platform concepts only; provider-native translation remains
later work.

Review, implement, and dependency are security-class neutral.  Their effective
security class is inherited from the platform/project policy and may be
strengthened by later restrictions.  The explicit ``compatibility`` profile is
the legacy-compatibility bundle and pins ``security_class=compatibility``; the
monotonic resolver therefore rejects it when an upper layer requires hardened
execution.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping, Sequence

_PROFILE_IDS = ("review", "implement", "dependency", "compatibility")


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """One trusted built-in sparse execution-policy layer."""

    id: str
    _restrictions: Mapping[str, object]
    requires_task_shell_destinations: bool = False

    def __post_init__(self) -> None:
        if self.id not in _PROFILE_IDS:
            raise ValueError(f"unsupported built-in execution profile {self.id!r}")
        if not isinstance(self._restrictions, Mapping):
            raise TypeError("profile restrictions must be a mapping")

    def restrictions(
        self,
        *,
        task_shell_destinations: Sequence[str] | None = None,
    ) -> dict[str, object]:
        """Materialize a fresh sparse layer for ``PolicyResolver``.

        Dependency execution requires an explicit caller-supplied destination
        allowlist.  Other profiles reject destination input so a caller cannot
        accidentally turn a non-network profile into a dependency run.
        """

        result = deepcopy(dict(self._restrictions))
        if self.requires_task_shell_destinations:
            if (
                task_shell_destinations is None
                or isinstance(task_shell_destinations, (str, bytes))
                or not isinstance(task_shell_destinations, Sequence)
                or len(task_shell_destinations) == 0
            ):
                raise ValueError(
                    "dependency profile requires explicit task-shell destinations"
                )
            network = result["network"]
            assert isinstance(network, dict)
            task_shell = network["task_shell"]
            assert isinstance(task_shell, dict)
            task_shell["destinations"] = list(task_shell_destinations)
        elif task_shell_destinations is not None:
            raise ValueError(
                f"{self.id} profile does not accept task-shell destinations"
            )
        return result


_REVIEW = ExecutionProfile(
    id="review",
    _restrictions={
        "workspace": {"access": "read"},
        "reference": {"access": "read"},
        "network": {"task_shell": {"mode": "deny"}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"commit": False},
        "sandbox": {"required": True},
    },
)

_IMPLEMENT = ExecutionProfile(
    id="implement",
    _restrictions={
        "workspace": {"access": "write"},
        "reference": {"access": "read"},
        "network": {"task_shell": {"mode": "deny"}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"read": True, "commit": True, "push": False},
        "sandbox": {"required": True},
    },
)

_DEPENDENCY = ExecutionProfile(
    id="dependency",
    _restrictions={
        "workspace": {"access": "write"},
        "network": {"task_shell": {"mode": "allowlist"}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"commit": True, "push": False},
        "sandbox": {"required": True},
    },
    requires_task_shell_destinations=True,
)

_COMPATIBILITY = ExecutionProfile(
    id="compatibility",
    _restrictions={"security_class": "compatibility"},
)

_BUILTIN_PROFILES = (_REVIEW, _IMPLEMENT, _DEPENDENCY, _COMPATIBILITY)
_PROFILE_BY_ID = {profile.id: profile for profile in _BUILTIN_PROFILES}


def list_profiles() -> tuple[ExecutionProfile, ...]:
    """Return trusted built-ins in deterministic public order."""

    return _BUILTIN_PROFILES


def get_profile(profile_id: str) -> ExecutionProfile:
    """Return one trusted built-in profile by ID."""

    if not isinstance(profile_id, str):
        raise TypeError("profile_id must be a string")
    try:
        return _PROFILE_BY_ID[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown execution profile {profile_id!r}") from exc
