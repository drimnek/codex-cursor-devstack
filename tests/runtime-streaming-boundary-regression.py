#!/usr/bin/env python3
"""Regression coverage for the RT-004 streaming/process-control boundary."""
from __future__ import annotations

import io
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

import agentdev.broker.daemon as agentd  # noqa: E402
import agentdev.runtime.podman as podman_runtime  # noqa: E402
from agentdev.agents.base import AuthSpec  # noqa: E402
from agentdev.runtime.base import RuntimeControl, RuntimeResult  # noqa: E402


class MemoryIO:
    def __init__(self, controls=None) -> None:
        self.output: list[bytes] = []
        self.controls = list(controls or [])

    def write_output(self, data: bytes) -> None:
        self.output.append(data)

    def receive_control(self, timeout_seconds: float | None = None):
        del timeout_seconds
        if not self.controls:
            return None
        return self.controls.pop(0)


class FakeConn:
    def __init__(self) -> None:
        self.sent = bytearray()
        self.fileobj = io.BytesIO()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def getsockopt(self, _level, _option, _length):
        return struct.pack("3i", 1234, 1000, 1000)


def frames(conn: FakeConn) -> list[dict]:
    return [
        json.loads(line)
        for line in conn.sent.decode().splitlines()
        if line.strip()
    ]


def check_noninteractive_streaming() -> None:
    original_popen = podman_runtime.subprocess.Popen
    calls: list[tuple[list[str], dict]] = []

    class FakeStdout:
        def __init__(self) -> None:
            self.chunks = [b"one", b"two", b""]

        def read(self, _size: int) -> bytes:
            return self.chunks.pop(0)

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = FakeStdout()

        def wait(self) -> int:
            return 7

    try:
        def fake_popen(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            return FakeProc()

        podman_runtime.subprocess.Popen = fake_popen
        runtime_io = MemoryIO()
        result = podman_runtime.run_noninteractive_argv(
            ["podman", "run", "--rm", "image", "tool"],
            runtime_io,
        )
        assert result == RuntimeResult(7)
        assert runtime_io.output == [b"one", b"two"]
        assert calls[0][0][:3] == ["podman", "run", "--rm"]
        assert calls[0][1]["stdin"] is subprocess.DEVNULL
        assert calls[0][1]["stdout"] is subprocess.PIPE
        assert calls[0][1]["stderr"] is subprocess.STDOUT
    finally:
        podman_runtime.subprocess.Popen = original_popen


def check_dispatch_boundary() -> None:
    original_noninteractive = podman_runtime.run_noninteractive_argv
    original_interactive = podman_runtime.run_interactive_argv
    original_cidfile = podman_runtime.new_interactive_cidfile
    original_add = podman_runtime.add_cidfile
    calls: list[tuple] = []

    try:
        podman_runtime.run_noninteractive_argv = (
            lambda argv, runtime_io, **kwargs:
            calls.append(("noninteractive", list(argv), runtime_io, kwargs))
            or RuntimeResult(3)
        )
        podman_runtime.run_interactive_argv = (
            lambda argv, runtime_io, **kwargs:
            calls.append(("interactive", list(argv), runtime_io, kwargs))
            or RuntimeResult(4)
        )

        with tempfile.TemporaryDirectory() as td:
            runtime_io = MemoryIO()
            assert podman_runtime.run_podman_argv(
                ["podman", "run", "--rm", "image"],
                runtime_io,
                state_dir=Path(td),
                interactive=False,
            ) == RuntimeResult(3)
            assert calls[-1][0] == "noninteractive"

            cidfile = Path(td) / "interactive.cid"
            podman_runtime.new_interactive_cidfile = lambda _state_dir: cidfile
            podman_runtime.add_cidfile = (
                lambda argv, path: [*argv[:2], "--cidfile", str(path), *argv[2:]]
            )
            assert podman_runtime.run_podman_argv(
                ["podman", "run", "--rm", "image"],
                runtime_io,
                state_dir=Path(td),
                interactive=True,
                timeout_seconds=9,
                timeout_output=b"timeout\n",
            ) == RuntimeResult(4)
            kind, argv, seen_io, kwargs = calls[-1]
            assert kind == "interactive"
            assert seen_io is runtime_io
            assert argv[:4] == ["podman", "run", "--cidfile", str(cidfile)]
            assert kwargs["cidfile"] == cidfile
            assert kwargs["timeout_seconds"] == 9
            assert kwargs["timeout_output"] == b"timeout\n"

        try:
            podman_runtime.run_podman_argv(
                ["podman", "run", "image"],
                MemoryIO(),
                state_dir=Path("/tmp"),
                interactive=False,
                timeout_seconds=1,
            )
        except ValueError as exc:
            assert "noninteractive" in str(exc)
        else:
            raise AssertionError("noninteractive timeout was silently ignored")
    finally:
        podman_runtime.run_noninteractive_argv = original_noninteractive
        podman_runtime.run_interactive_argv = original_interactive
        podman_runtime.new_interactive_cidfile = original_cidfile
        podman_runtime.add_cidfile = original_add


def check_interactive_input_resize_and_cancel() -> None:
    originals = {
        "openpty": podman_runtime.pty.openpty,
        "popen": podman_runtime.subprocess.Popen,
        "select": podman_runtime.select.select,
        "write": podman_runtime.os.write,
        "winsize": podman_runtime.set_winsize,
    }
    writes: list[tuple[int, bytes]] = []
    resizes: list[tuple[int, int, int]] = []

    class FakeProc:
        def __init__(self) -> None:
            self.poll_calls = 0

        def poll(self):
            self.poll_calls += 1
            return 0 if self.poll_calls >= 2 else None

        def wait(self, timeout=None):
            del timeout
            return 0

    try:
        rfd, wfd = os.pipe()
        podman_runtime.pty.openpty = lambda: (rfd, wfd)
        podman_runtime.subprocess.Popen = lambda *_args, **_kwargs: FakeProc()
        podman_runtime.select.select = lambda *_args, **_kwargs: ([], [], [])
        podman_runtime.os.write = lambda fd, data: writes.append((fd, data)) or len(data)
        podman_runtime.set_winsize = (
            lambda fd, rows, cols: resizes.append((fd, rows, cols))
        )

        runtime_io = MemoryIO(
            [
                RuntimeControl("input", b"hello"),
                RuntimeControl("resize", rows=33, cols=101),
            ]
        )
        result = podman_runtime.run_interactive_argv(
            ["fake"],
            runtime_io,
            terminate=lambda _proc: None,
            cleanup=lambda _cidfile: None,
        )
        assert result == RuntimeResult(0)
        assert writes == [(rfd, b"hello")]
        assert resizes == [(rfd, 33, 101)]
    finally:
        podman_runtime.pty.openpty = originals["openpty"]
        podman_runtime.subprocess.Popen = originals["popen"]
        podman_runtime.select.select = originals["select"]
        podman_runtime.os.write = originals["write"]
        podman_runtime.set_winsize = originals["winsize"]

    original_openpty = podman_runtime.pty.openpty
    original_popen = podman_runtime.subprocess.Popen
    original_select = podman_runtime.select.select
    terminated: list[object] = []
    cleaned: list[Path | None] = []

    class CancelProc:
        def __init__(self) -> None:
            self.done = False

        def poll(self):
            return 0 if self.done else None

        def wait(self, timeout=None):
            del timeout
            return 0

    proc = CancelProc()

    def terminate(item) -> None:
        terminated.append(item)
        item.done = True

    try:
        rfd, wfd = os.pipe()
        podman_runtime.pty.openpty = lambda: (rfd, wfd)
        podman_runtime.subprocess.Popen = lambda *_args, **_kwargs: proc
        podman_runtime.select.select = lambda *_args, **_kwargs: ([], [], [])
        cidfile = Path("/tmp/rt004-cancel.cid")
        result = podman_runtime.run_interactive_argv(
            ["fake"],
            MemoryIO([RuntimeControl("cancel")]),
            cidfile=cidfile,
            terminate=terminate,
            cleanup=lambda path: cleaned.append(path),
        )
        assert result == RuntimeResult(130)
        assert terminated == [proc]
        assert cleaned == [cidfile]
    finally:
        podman_runtime.pty.openpty = original_openpty
        podman_runtime.subprocess.Popen = original_popen
        podman_runtime.select.select = original_select


def check_signal_escalation() -> None:
    original_killpg = podman_runtime.os.killpg
    signals: list[int] = []

    class FakeProc:
        pid = 4242

        def __init__(self) -> None:
            self.wait_calls = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return 0

    try:
        podman_runtime.os.killpg = lambda _pid, sig: signals.append(sig)
        podman_runtime.terminate_process_group(
            FakeProc(),
            int_grace=0.0,
            term_grace=0.0,
        )
        assert signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
    finally:
        podman_runtime.os.killpg = original_killpg


def check_driver_declares_auth_interaction() -> None:
    originals = {
        "registered_provider": agentd.registered_provider,
        "seed_provider_home": agentd.seed_provider_home,
        "common_runtime_args": agentd.common_runtime_args,
        "execute_runtime_argv": agentd.execute_runtime_argv,
    }
    captured: list[dict] = []

    class FakeDriver:
        def __init__(self, interactive: bool) -> None:
            self.interactive = interactive

        def auth_spec(self) -> AuthSpec:
            return AuthSpec(
                ("thirdctl", "authenticate"),
                interactive=self.interactive,
                timeout_seconds=123 if self.interactive else None,
            )

    current = {"driver": FakeDriver(False)}
    try:
        agentd.registered_provider = lambda *_args, **_kwargs: current["driver"]
        agentd.seed_provider_home = lambda *_args, **_kwargs: None
        agentd.common_runtime_args = (
            lambda *_args, **_kwargs: ["podman", "run", "--rm"]
        )

        def fake_execute(_cfg, _conn, _fileobj, argv, **kwargs):
            captured.append({"argv": list(argv), **kwargs})
            return 19

        agentd.execute_runtime_argv = fake_execute
        cfg = {
            "images": {"third": "third-image"},
            "state_dir": "/tmp/runtime-state",
        }

        conn = FakeConn()
        assert agentd.op_auth(cfg, conn, conn.fileobj, "third") == 19
        assert frames(conn) == [
            {"type": "start", "interactive": False},
            {"type": "exit", "code": 19},
        ]
        assert captured[-1]["interactive"] is False
        assert captured[-1]["argv"][-3:] == [
            "third-image", "thirdctl", "authenticate"
        ]

        current["driver"] = FakeDriver(True)
        conn = FakeConn()
        assert agentd.op_auth(cfg, conn, conn.fileobj, "third") == 19
        assert frames(conn) == [
            {"type": "start", "interactive": True},
            {"type": "exit", "code": 19},
        ]
        assert captured[-1]["interactive"] is True
        assert captured[-1]["timeout_seconds"] == 123
    finally:
        for name, value in originals.items():
            setattr(agentd, name, value)


def check_source_boundary() -> None:
    daemon_source = (
        PLATFORM_SRC / "agentdev/broker/daemon.py"
    ).read_text(encoding="utf-8")
    podman_source = (
        PLATFORM_SRC / "agentdev/runtime/podman.py"
    ).read_text(encoding="utf-8")

    op_auth_source = daemon_source[
        daemon_source.index("def op_auth("):daemon_source.index("\ndef op_status(", daemon_source.index("def op_auth("))
    ]
    for process_mechanic in (
        "new_interactive_cidfile(",
        "add_cidfile(",
        "stream_interactive(",
        "pty.openpty(",
        "os.killpg(",
        "subprocess.Popen(",
    ):
        assert process_mechanic not in op_auth_source, process_mechanic

    for runtime_mechanic in (
        "def run_noninteractive_argv(",
        "def run_interactive_argv(",
        "def terminate_process_group(",
        "def run_podman_argv(",
        "pty.openpty()",
        "os.killpg(",
    ):
        assert runtime_mechanic in podman_source, runtime_mechanic

    for agent_path in (PLATFORM_SRC / "agentdev/agents").glob("*.py"):
        source = agent_path.read_text(encoding="utf-8")
        for forbidden in (
            "pty.openpty(",
            "subprocess.Popen(",
            "os.killpg(",
            "--cidfile",
        ):
            assert forbidden not in source, f"{agent_path.name}: {forbidden}"


def main() -> None:
    check_noninteractive_streaming()
    check_dispatch_boundary()
    check_interactive_input_resize_and_cancel()
    check_signal_escalation()
    check_driver_declares_auth_interaction()
    check_source_boundary()
    print("runtime streaming boundary regression checks passed")


if __name__ == "__main__":
    main()
