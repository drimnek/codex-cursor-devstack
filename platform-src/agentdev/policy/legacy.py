"""Compatibility mapping between legacy run flags and execution-profile intent.

MA2-POL-005 keeps the existing CLI/RPC wire contract while centralizing the
meaning of ``readonly`` and ``outer_only``.  The public profile-based CLI is a
later requirement; this adapter already accepts an explicit profile so that the
future path can reuse the same conflict rules instead of inventing another
merge policy.

Legacy runs are compatibility-class by definition at this migration stage.  A
legacy ``readonly`` request selects review intent; otherwise it selects
implement intent.  ``outer_only`` remains an orthogonal provider compatibility
modifier and therefore may be combined with a read-only review run.
"""
from __future__ import annotations

from dataclasses import dataclass

from .profiles import get_profile


class LegacyProfileConflictError(ValueError):
    """Raised when an explicit profile contradicts a legacy alias."""


@dataclass(frozen=True, slots=True)
class RunProfileMapping:
    """Deterministic normalized intent for one run request."""

    profile_id: str
    readonly: bool
    outer_only: bool
    security_class: str | None


def _optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise TypeError(f"{label} must be boolean or None")
    return value


def resolve_run_profile_request(
    *,
    profile: str | None = None,
    readonly: bool | None = None,
    outer_only: bool | None = None,
) -> RunProfileMapping:
    """Normalize legacy aliases and a future explicit profile request.

    ``None`` means the corresponding legacy alias was not supplied.  Current
    broker calls preserve the historical bool-coercion behavior before invoking
    this helper.  A future direct profile path can omit aliases entirely and use
    the same conflict checks.
    """

    readonly = _optional_bool(readonly, "readonly")
    outer_only = _optional_bool(outer_only, "outer_only")
    effective_outer_only = False if outer_only is None else outer_only

    if profile is None:
        effective_readonly = False if readonly is None else readonly
        return RunProfileMapping(
            profile_id="review" if effective_readonly else "implement",
            readonly=effective_readonly,
            outer_only=effective_outer_only,
            security_class="compatibility",
        )

    if not isinstance(profile, str):
        raise TypeError("profile must be a string or None")
    get_profile(profile)

    if profile == "review":
        if readonly is False:
            raise LegacyProfileConflictError(
                "profile 'review' conflicts with legacy readonly=False"
            )
        effective_readonly = True
    elif profile in {"implement", "dependency"}:
        if readonly is True:
            raise LegacyProfileConflictError(
                f"profile {profile!r} conflicts with legacy readonly=True"
            )
        effective_readonly = False
    else:
        # Compatibility is a security-class overlay, so workspace access remains
        # inherited unless a legacy readonly alias explicitly narrows it.
        effective_readonly = False if readonly is None else readonly

    security_class = (
        "compatibility"
        if profile == "compatibility" or effective_outer_only
        else None
    )
    return RunProfileMapping(
        profile_id=profile,
        readonly=effective_readonly,
        outer_only=effective_outer_only,
        security_class=security_class,
    )
