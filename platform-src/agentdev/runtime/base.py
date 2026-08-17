"""Provider-neutral runtime backend contract.

Runtime backends consume a fully resolved execution plan. They own runtime
translation and process/PTY lifecycle, but they do not interpret provider CLI
semantics or project/task domain state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentdev.execution.plan import ResolvedExecutionPlan


_CONTROL_KINDS = frozenset({"input", "resize", "cancel"})


@dataclass(frozen=True, slots=True)
class RuntimeControl:
    """One provider-neutral control event for an interactive runtime."""

    kind: str
    data: bytes = b""
    rows: int | None = None
    cols: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in _CONTROL_KINDS:
            raise ValueError(f"unsupported runtime control kind {self.kind!r}")
        if not isinstance(self.data, bytes):
            raise ValueError("runtime control data must be bytes")
        if self.kind == "input":
            if self.rows is not None or self.cols is not None:
                raise ValueError("input control must not contain terminal dimensions")
        elif self.kind == "resize":
            if self.data:
                raise ValueError("resize control must not contain input data")
            if type(self.rows) is not int or type(self.cols) is not int:
                raise ValueError("resize control requires integer rows and cols")
            if self.rows <= 0 or self.cols <= 0:
                raise ValueError("resize dimensions must be positive")
        else:
            if self.data or self.rows is not None or self.cols is not None:
                raise ValueError("cancel control must not contain payload")


@runtime_checkable
class RuntimeIO(Protocol):
    """I/O boundary exposed to an executing runtime backend.

    RPC framing remains outside the runtime layer. The backend only receives
    normalized control events and emits raw provider output bytes.
    """

    def write_output(self, data: bytes) -> None:
        """Emit provider/runtime output."""

    def receive_control(self, timeout_seconds: float | None = None) -> RuntimeControl | None:
        """Return the next control event, or None if no event is available before timeout."""


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Normalized completion result returned by a runtime backend."""

    exit_code: int

    def __post_init__(self) -> None:
        if type(self.exit_code) is not int:
            raise ValueError("runtime exit_code must be an integer")


class RuntimeBackend(ABC):
    """Trusted execution backend for resolved plans.

    Implementations may translate the plan into Podman, a VM, or another
    trusted executor. They must not reinterpret provider command semantics.
    """

    @abstractmethod
    def id(self) -> str:
        """Return the stable runtime backend identifier."""

    @abstractmethod
    def execute(
        self,
        plan: ResolvedExecutionPlan,
        io: RuntimeIO,
    ) -> RuntimeResult:
        """Execute one resolved plan and return a normalized result."""
