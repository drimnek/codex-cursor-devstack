"""Provider-neutral outer-runtime isolation requirements."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeIsolationRequirements:
    """Declarative requirements needed before provider-native sandbox startup."""

    uid: int | None = None
    gid: int | None = None
    nested_sandbox_bootstrap: bool = False

    def __post_init__(self) -> None:
        if (self.uid is None) != (self.gid is None):
            raise ValueError("uid and gid must be set together")
        for name in ("uid", "gid"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if type(self.nested_sandbox_bootstrap) is not bool:
            raise ValueError("nested_sandbox_bootstrap must be boolean")
        if self.nested_sandbox_bootstrap and self.uid is None:
            raise ValueError("nested sandbox bootstrap requires an explicit non-root identity")
        if self.nested_sandbox_bootstrap and self.uid == 0:
            raise ValueError("nested sandbox bootstrap requires a non-root uid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "gid": self.gid,
            "nested_sandbox_bootstrap": self.nested_sandbox_bootstrap,
        }
