#!/usr/bin/env python3
"""Regression coverage for the concrete Podman RuntimeBackend."""
from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

import agentdev.broker.runtime_io as broker_runtime_io  # noqa: E402
import agentdev.runtime.podman as podman_runtime  # noqa: E402
from agentdev.execution.plan import (  # noqa: E402
    ExecutionMount,
    NetworkRuntimeRequirements,
    ResolvedExecutionPlan,
    ResolvedProviderPolicyArtifacts,
    ResourceLimits,
)
from agentdev.runtime.base import RuntimeControl, RuntimeResult  # noqa: E402


class MemoryIO:
    def __init__(self) -> None:
        self.output: list[bytes] = []

    def write_output(self, data: bytes) -> None:
        self.output.append(data)

    def receive_control(self, timeout_seconds: float | None = None):
        del timeout_seconds
        return None


class FakeConn:
    def __init__(self, frame: dict | None = None) -> None:
        payload = b""
        if frame is not None:
            payload = json.dumps(frame, separators=(",", ":")).encode() + b"\n"
        self.fileobj = io.BytesIO(payload)
        self.sent = bytearray()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def getsockopt(self, _level, _option, _length):
        return struct.pack("3i", 1234, 1000, 1000)


def plan(*, interaction_mode: str = "interactive") -> ResolvedExecutionPlan:
    return ResolvedExecutionPlan(
        agent_id="third-provider",
        image="third-image",
        argv=("thirdctl", "run", "review"),
        environment=(("AGENT_TASK_ID", "REQ-9"), ("THIRD_MODE", "safe")),
        workspace_mount=ExecutionMount(
            "/host/workspace", "/workspace", True, "workspace"
        ),
        reference_mounts=(
            ExecutionMount("/host/reference", "/reference", True, "reference"),
        ),
        task_metadata_mount=ExecutionMount(
            "/host/task.json", "/task/metadata.json", True, "task-metadata"
        ),
        provider_state_mounts=(
            ExecutionMount("third-state", "/var/lib/third", False, "provider-state"),
        ),
        provider_policy_artifacts=ResolvedProviderPolicyArtifacts(
            mounts=(
                ExecutionMount(
                    "/trusted/policy", "/etc/third/policy", True, "provider-policy"
                ),
            ),
        ),
        resource_limits=ResourceLimits(256, "4g", 2),
        network=NetworkRuntimeRequirements(
            "slirp4netns:allow_host_loopback=false", http_proxy=False
        ),
        readonly=True,
        interaction_mode=interaction_mode,
        required_capabilities=frozenset({"workspace:readonly"}),
    )


def check_argv_translation() -> None:
    argv = podman_runtime.execution_plan_argv(plan())
    assert argv[:3] == ["podman", "run", "--rm"]
    assert "--network=slirp4netns:allow_host_loopback=false" in argv
    assert "--http-proxy=false" in argv
    assert "--read-only" in argv
    assert "--cap-drop=all" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--pids-limit=256" in argv
    assert "--memory=4g" in argv
    assert "--cpus=2" in argv
    assert "/host/workspace:/workspace:ro" in argv
    assert "/host/reference:/reference:ro" in argv
    assert "/host/task.json:/task/metadata.json:ro" in argv
    assert "third-state:/var/lib/third:rw" in argv
    assert "/trusted/policy:/etc/third/policy:ro" in argv
    assert "AGENT_TASK_ID=REQ-9" in argv
    assert "THIRD_MODE=safe" in argv
    assert argv[-4:] == ["third-image", "thirdctl", "run", "review"]


def check_backend_dispatch() -> None:
    original_noninteractive = podman_runtime.run_noninteractive_argv
    original_interactive = podman_runtime.run_interactive_argv
    original_cidfile = podman_runtime.new_interactive_cidfile
    original_add = podman_runtime.add_cidfile
    calls: list[tuple] = []

    try:
        podman_runtime.run_noninteractive_argv = (
            lambda argv, runtime_io, **_kwargs:
            calls.append(("noninteractive", list(argv), runtime_io)) or RuntimeResult(7)
        )
        with tempfile.TemporaryDirectory() as td:
            backend = podman_runtime.PodmanBackend(Path(td))
            runtime_io = MemoryIO()
            result = backend.execute(plan(interaction_mode="noninteractive"), runtime_io)
            assert backend.id() == "podman"
            assert result == RuntimeResult(7)
            assert calls[-1][0] == "noninteractive"
            assert calls[-1][1][:3] == ["podman", "run", "--rm"]

        with tempfile.TemporaryDirectory() as td:
            cidfile = Path(td) / "runtime.cid"
            podman_runtime.new_interactive_cidfile = lambda _state: cidfile
            podman_runtime.add_cidfile = (
                lambda argv, path: [*argv[:2], "--cidfile", str(path), *argv[2:]]
            )
            podman_runtime.run_interactive_argv = (
                lambda argv, runtime_io, **kwargs:
                calls.append(("interactive", list(argv), runtime_io, kwargs)) or RuntimeResult(9)
            )
            backend = podman_runtime.PodmanBackend(Path(td))
            runtime_io = MemoryIO()
            result = backend.execute(plan(), runtime_io)
            assert result == RuntimeResult(9)
            kind, argv, seen_io, kwargs = calls[-1]
            assert kind == "interactive"
            assert seen_io is runtime_io
            assert argv[:4] == ["podman", "run", "--cidfile", str(cidfile)]
            assert kwargs["cidfile"] == cidfile
    finally:
        podman_runtime.run_noninteractive_argv = original_noninteractive
        podman_runtime.run_interactive_argv = original_interactive
        podman_runtime.new_interactive_cidfile = original_cidfile
        podman_runtime.add_cidfile = original_add


def check_rpc_runtime_io() -> None:
    original_select = broker_runtime_io.select.select
    try:
        conn = FakeConn({"type": "input", "data": "aGVsbG8="})
        broker_runtime_io.select.select = lambda *_args, **_kwargs: ([conn], [], [])
        runtime_io = broker_runtime_io.RpcRuntimeIO(conn, conn.fileobj)
        assert runtime_io.receive_control(0.0) == RuntimeControl("input", b"hello")

        conn = FakeConn({"type": "resize", "rows": 24, "cols": 80})
        broker_runtime_io.select.select = lambda *_args, **_kwargs: ([conn], [], [])
        runtime_io = broker_runtime_io.RpcRuntimeIO(conn, conn.fileobj)
        assert runtime_io.receive_control(0.0) == RuntimeControl(
            "resize", rows=24, cols=80
        )

        conn = FakeConn({"type": "cancel"})
        broker_runtime_io.select.select = lambda *_args, **_kwargs: ([conn], [], [])
        runtime_io = broker_runtime_io.RpcRuntimeIO(conn, conn.fileobj)
        assert runtime_io.receive_control(0.0) == RuntimeControl("cancel")

        conn = FakeConn()
        broker_runtime_io.select.select = lambda *_args, **_kwargs: ([], [], [])
        runtime_io = broker_runtime_io.RpcRuntimeIO(conn, conn.fileobj)
        assert runtime_io.receive_control(0.0) is None
    finally:
        broker_runtime_io.select.select = original_select


def check_source_boundary() -> None:
    daemon = (PLATFORM_SRC / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    podman = (PLATFORM_SRC / "agentdev/runtime/podman.py").read_text(encoding="utf-8")
    rpc_io = (PLATFORM_SRC / "agentdev/broker/runtime_io.py").read_text(encoding="utf-8")

    for moved_definition in (
        "def execution_plan_argv(",
        "def set_winsize(",
        "def terminate_process_group(",
        "def add_cidfile(",
        "def cleanup_interactive_container(",
    ):
        assert moved_definition not in daemon, moved_definition

    assert "class PodmanBackend" in podman
    assert "pty.openpty()" in podman
    assert "os.killpg(" in podman
    assert "--cap-drop=all" in podman
    assert "--security-opt=no-new-privileges" in podman
    assert "RpcRuntimeIO" in daemon
    assert "PodmanBackend" in daemon

    for provider_literal in (
        "codex exec",
        "codex login",
        "agent --trust",
        "NO_OPEN_BROWSER",
        "/root/.codex",
        "/root/.cursor",
        "/root/.config/cursor",
    ):
        assert provider_literal not in podman, provider_literal

    assert "recv_json_line" not in podman
    assert "send_output" not in podman
    assert "recv_json_line" in rpc_io
    assert "send_output" in rpc_io


def main() -> None:
    check_argv_translation()
    check_backend_dispatch()
    check_rpc_runtime_io()
    check_source_boundary()
    print("Podman backend regression checks passed")


if __name__ == "__main__":
    main()
