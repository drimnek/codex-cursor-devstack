#!/usr/bin/env python3
"""Regression coverage for the provider-neutral RuntimeBackend contract."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.execution.plan import (  # noqa: E402
    ExecutionMount,
    NetworkRuntimeRequirements,
    ResolvedExecutionPlan,
    ResolvedProviderPolicyArtifacts,
    ResourceLimits,
)
from agentdev.runtime.base import (  # noqa: E402
    RuntimeBackend,
    RuntimeControl,
    RuntimeIO,
    RuntimeResult,
)


class MemoryIO:
    def __init__(self, controls: list[RuntimeControl | None] | None = None) -> None:
        self.output: list[bytes] = []
        self.controls = list(controls or [])

    def write_output(self, data: bytes) -> None:
        self.output.append(data)

    def receive_control(self, timeout_seconds: float | None = None) -> RuntimeControl | None:
        del timeout_seconds
        if not self.controls:
            return None
        return self.controls.pop(0)


class FakeRuntimeBackend(RuntimeBackend):
    def __init__(self) -> None:
        self.seen_plan: ResolvedExecutionPlan | None = None
        self.controls: list[RuntimeControl | None] = []

    def id(self) -> str:
        return "fake-runtime"

    def execute(self, plan: ResolvedExecutionPlan, io: RuntimeIO) -> RuntimeResult:
        self.seen_plan = plan
        io.write_output(b"runtime:" + plan.agent_id.encode())
        if plan.interaction_mode == "interactive":
            self.controls.append(io.receive_control(0.0))
        return RuntimeResult(17)


def mount(source: str, target: str, ro: bool, role: str) -> ExecutionMount:
    return ExecutionMount(source, target, ro, role)


def sample_plan(*, interaction_mode: str = "interactive") -> ResolvedExecutionPlan:
    return ResolvedExecutionPlan(
        agent_id="third-provider",
        image="third-image",
        argv=("thirdctl", "run", "--mode", "review"),
        environment=(("AGENT_TASK_ID", "REQ-7"),),
        workspace_mount=mount("/host/workspace", "/workspace", True, "workspace"),
        reference_mounts=(),
        task_metadata_mount=mount(
            "/host/tasks/REQ-7.json",
            "/task/metadata.json",
            True,
            "task-metadata",
        ),
        provider_state_mounts=(
            mount("third-state", "/var/lib/third", False, "provider-state"),
        ),
        provider_policy_artifacts=ResolvedProviderPolicyArtifacts(),
        resource_limits=ResourceLimits(128, "2g", 1),
        network=NetworkRuntimeRequirements("slirp4netns:allow_host_loopback=false"),
        readonly=True,
        interaction_mode=interaction_mode,
        required_capabilities=frozenset({"workspace:readonly"}),
    )


def check_contract() -> None:
    assert inspect.isabstract(RuntimeBackend)

    try:
        RuntimeBackend()
    except TypeError:
        pass
    else:
        raise AssertionError("abstract RuntimeBackend was instantiated")

    assert isinstance(MemoryIO(), RuntimeIO)

    backend = FakeRuntimeBackend()
    plan = sample_plan()
    io = MemoryIO([RuntimeControl("input", b"hello")])
    result = backend.execute(plan, io)

    assert backend.id() == "fake-runtime"
    assert backend.seen_plan is plan
    assert result == RuntimeResult(17)
    assert io.output == [b"runtime:third-provider"]
    assert backend.controls == [RuntimeControl("input", b"hello")]


def check_control_validation() -> None:
    assert RuntimeControl("input", b"x").data == b"x"
    assert RuntimeControl("resize", rows=24, cols=80).rows == 24
    assert RuntimeControl("cancel").kind == "cancel"

    invalid = (
        lambda: RuntimeControl("unknown"),
        lambda: RuntimeControl("input", b"x", rows=1, cols=1),
        lambda: RuntimeControl("resize", b"x", rows=24, cols=80),
        lambda: RuntimeControl("resize", rows=0, cols=80),
        lambda: RuntimeControl("cancel", b"x"),
        lambda: RuntimeResult(True),
    )
    for create in invalid:
        try:
            create()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid runtime contract value was accepted")


def check_noninteractive_contract() -> None:
    backend = FakeRuntimeBackend()
    plan = sample_plan(interaction_mode="noninteractive")
    io = MemoryIO()
    assert backend.execute(plan, io) == RuntimeResult(17)
    assert backend.controls == []


def check_source_boundary() -> None:
    source = (PLATFORM_SRC / "agentdev/runtime/base.py").read_text(encoding="utf-8")

    assert "ResolvedExecutionPlan" in source
    assert "RuntimeIO" in source
    assert "RuntimeResult" in source

    forbidden = (
        '"podman", "run"',
        "subprocess",
        "agentdev.agents.codex",
        "agentdev.agents.cursor",
        "/root/.codex",
        "/root/.cursor",
        "/root/.config/cursor",
        "repo/agent",
        "task_records",
    )
    lower_source = source.lower()
    for marker in forbidden:
        assert marker.lower() not in lower_source, marker


def main() -> None:
    check_contract()
    check_control_validation()
    check_noninteractive_contract()
    check_source_boundary()
    print("runtime backend contract regression checks passed")


if __name__ == "__main__":
    main()
