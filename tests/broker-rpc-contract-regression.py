#!/usr/bin/env python3
"""Characterize the v0.1 agentctl <-> agentd RPC contract before modularization."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


agentd = load_script("agentd_rpc_contract", ROOT / "platform-src/bin/agentd")
agentctl = load_script("agentctl_rpc_contract", ROOT / "platform-src/bin/agentctl")
agentd.LOG.disabled = True

import agentdev.broker.runtime_io as broker_runtime_io
import agentdev.runtime.podman as podman_runtime


class FakeConn:
    def __init__(self, request: dict | None = None):
        payload = b""
        if request is not None:
            payload = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        self.fileobj = io.BytesIO(payload)
        self.sent = bytearray()

    def makefile(self, _mode: str):
        return self.fileobj

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def getsockopt(self, _level, _option, _length):
        return struct.pack("3i", 1234, 1000, 1000)

    def close(self) -> None:
        pass


def frames(conn: FakeConn) -> list[dict]:
    return [json.loads(line) for line in bytes(conn.sent).splitlines() if line]


def expect_request_error(message: str, func) -> None:
    try:
        func()
    except agentd.RequestError as exc:
        assert str(exc) == message, (str(exc), message)
    else:
        raise AssertionError(f"expected RequestError: {message}")


def check_request_surface() -> None:
    expected_fields = {
        "ping": {"op"},
        "build": {"op"},
        "status": {"op"},
        "versions": {"op"},
        "smoke": {"op"},
        "auth": {"op", "provider"},
        "index": {"op", "project", "task"},
        "run": {"op", "provider", "project", "task", "readonly", "outer_only", "prompt"},
        "project-init": {"op", "project", "bundle"},
        "project-sync": {"op", "project", "bundle"},
        "project-export": {"op", "project"},
        "project-status": {"op", "project"},
        "task-start": {"op", "project", "task", "parallel", "dependencies"},
        "task-complete": {"op", "project", "task"},
        "task-merge": {"op", "project", "task"},
        "task-abort": {"op", "project", "task"},
        "task-list": {"op", "project"},
    }
    assert agentd.ALLOWED_OPS == set(expected_fields)
    assert agentd.REQUEST_FIELDS == expected_fields

    # Shape validation rejects unknown fields but leaves required-value checks
    # to the operation implementation.
    for op in expected_fields:
        agentd.validate_request_shape({"op": op})

    expect_request_error(
        "unsupported operation",
        lambda: agentd.validate_request_shape({"op": "not-an-operation"}),
    )
    expect_request_error(
        "unexpected RPC fields for ping: ['extra']",
        lambda: agentd.validate_request_shape({"op": "ping", "extra": True}),
    )
    expect_request_error(
        "unexpected RPC fields for run: ['extra']",
        lambda: agentd.validate_request_shape(
            {"op": "run", "provider": "codex", "project": "p", "task": "t", "extra": 1}
        ),
    )


def check_controller_run_request() -> None:
    args = SimpleNamespace(
        provider="codex",
        project="project",
        task="task",
        readonly=False,
        outer_only=False,
        prompt=None,
    )
    assert agentctl.runtime_request(args, "run") == {
        "op": "run",
        "provider": "codex",
        "project": "project",
        "task": "task",
        "readonly": False,
        "outer_only": False,
    }

    args.readonly = True
    args.outer_only = True
    args.prompt = "review this"
    assert agentctl.runtime_request(args, "run") == {
        "op": "run",
        "provider": "codex",
        "project": "project",
        "task": "task",
        "readonly": True,
        "outer_only": True,
        "prompt": "review this",
    }


def check_handle_framing() -> None:
    original_warning = agentd.LOG.warning
    original_exception = agentd.LOG.exception
    original_project_status = agentd.op_project_status
    warnings: list[str] = []
    exceptions: list[str] = []

    agentd.LOG.warning = lambda message, *args, **_kwargs: warnings.append(
        message % args if args else message
    )
    agentd.LOG.exception = lambda message, *args, **_kwargs: exceptions.append(
        message % args if args else message
    )

    try:
        ping = FakeConn({"op": "ping"})
        agentd.handle(ping, {})
        ping_frames = frames(ping)
        assert len(ping_frames) == 1
        assert ping_frames[0]["type"] == "result"
        assert ping_frames[0]["code"] == 0
        assert ping_frames[0]["result"]["status"] == "ok"
        assert isinstance(ping_frames[0]["result"]["uid"], int)

        unsupported = FakeConn({"op": "not-an-operation"})
        agentd.handle(unsupported, {})
        assert frames(unsupported) == [
            {"type": "error", "message": "unsupported operation", "code": 2}
        ]

        extra = FakeConn({"op": "ping", "extra": True})
        agentd.handle(extra, {})
        assert frames(extra) == [
            {
                "type": "error",
                "message": "unexpected RPC fields for ping: ['extra']",
                "code": 2,
            }
        ]

        assert warnings == [
            "request rejected: unsupported operation",
            "request rejected: unexpected RPC fields for ping: ['extra']",
        ]
        assert exceptions == []

        agentd.op_project_status = lambda _cfg, _req: (_ for _ in ()).throw(
            RuntimeError("sensitive internal detail")
        )
        internal = FakeConn({"op": "project-status", "project": "project"})
        agentd.handle(internal, {})
        assert frames(internal) == [
            {"type": "error", "message": "internal broker error", "code": 1}
        ]
        assert b"sensitive internal detail" not in internal.sent
        assert exceptions == ["request failed"]

        original_status = agentd.op_status
        try:
            agentd.op_status = lambda _cfg, _conn: 23
            status = FakeConn({"op": "status"})
            agentd.handle(status, {})
            assert frames(status) == [
                {"type": "start", "interactive": False},
                {"type": "exit", "code": 23},
            ]
        finally:
            agentd.op_status = original_status
    finally:
        agentd.LOG.warning = original_warning
        agentd.LOG.exception = original_exception
        agentd.op_project_status = original_project_status

def check_controller_error_codes() -> None:
    original_connect = agentctl.rpc_connect

    class ResponseSocket:
        def __init__(self, response: dict):
            self.fileobj = io.BytesIO(
                json.dumps(response, separators=(",", ":")).encode() + b"\n"
            )
            self.sent = bytearray()

        def makefile(self, _mode: str):
            return self.fileobj

        def sendall(self, data: bytes) -> None:
            self.sent.extend(data)

        def close(self) -> None:
            pass

    try:
        for code, message in (
            (2, "unsupported operation"),
            (1, "internal broker error"),
        ):
            sock = ResponseSocket({"type": "error", "message": message, "code": code})
            agentctl.rpc_connect = lambda _cfg, sock=sock: sock
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    agentctl.rpc({}, {"op": "ping"})
            except SystemExit as exc:
                assert exc.code == code
            else:
                raise AssertionError("expected agentctl.rpc to exit on first error frame")
            assert message in stderr.getvalue()
    finally:
        agentctl.rpc_connect = original_connect


def check_auth_contract() -> None:
    originals = {
        "seed_provider_home": agentd.seed_provider_home,
        "common_runtime_args": agentd.common_runtime_args,
        "execute_runtime_argv": agentd.execute_runtime_argv,
    }
    captured: list[list[str]] = []
    try:
        agentd.seed_provider_home = lambda _cfg, _provider: None
        agentd.common_runtime_args = lambda _cfg, _provider, **_kwargs: ["podman", "run", "--rm"]
        agentd.execute_runtime_argv = (
            lambda _cfg, _conn, _fileobj, argv, **_kwargs:
            captured.append(list(argv)) or 17
        )

        cfg = {"images": {"codex": "codex-image", "cursor": "cursor-image"}}

        codex = FakeConn()
        assert agentd.op_auth(cfg, codex, codex.fileobj, "codex") == 17
        assert frames(codex) == [
            {"type": "start", "interactive": True},
            {"type": "exit", "code": 17},
        ]
        assert captured[-1][-4:] == ["codex-image", "codex", "login", "--device-auth"]

        cursor = FakeConn()
        assert agentd.op_auth(cfg, cursor, cursor.fileobj, "cursor") == 17
        assert frames(cursor) == [
            {"type": "start", "interactive": True},
            {"type": "exit", "code": 17},
        ]
        argv = captured[-1]
        env_index = argv.index("-e")
        assert argv[env_index:env_index + 2] == ["-e", "NO_OPEN_BROWSER=1"]
        assert argv[-3:] == ["cursor-image", "agent", "login"]

        expect_request_error(
            "unsupported provider",
            lambda: agentd.op_auth(cfg, FakeConn(), io.BytesIO(), "gemini"),
        )
    finally:
        for name, value in originals.items():
            setattr(agentd, name, value)


def check_run_contract() -> None:
    originals = {
        "load_task": agentd.load_task,
        "seed_provider_home": agentd.seed_provider_home,
        "create_run_execution_plan": agentd.create_run_execution_plan,
        "execution_plan_argv": agentd.execution_plan_argv,
        "execute_runtime_plan": agentd.execute_runtime_plan,
        "lock_one": agentd.lock_one,
    }
    calls: list[dict] = []
    task_state = {
        "record": {
            "mode": "integration",
            "status": "active",
            "base_commit": "0123456789abcdef",
        }
    }

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        paths = {
            "tasks": root / "tasks",
            "reference": root / "reference",
            "agent": root / "repo-agent",
        }
        workspace = root / "workspace"
        for path in (*paths.values(), workspace):
            path.mkdir(parents=True, exist_ok=True)

        try:
            agentd.load_task = lambda _cfg, _project, _task: (
                dict(task_state["record"]),
                paths,
                workspace,
            )
            agentd.seed_provider_home = lambda _cfg, _provider: None

            def fake_create_plan(
                cfg, provider, context, run_spec, *, readonly, outer_only, reference, git_common
            ):
                required_capabilities = {
                    "workspace:readonly" if readonly else "workspace:writable",
                    "interactive-run",
                }
                if outer_only:
                    required_capabilities.add("compatibility:outer-only")
                plan = SimpleNamespace(
                    provider=provider,
                    context=context,
                    run_spec=run_spec,
                    readonly=readonly,
                    outer_only=outer_only,
                    reference=reference,
                    git_common=git_common,
                    image=cfg["images"][provider],
                    interaction_mode="interactive",
                    required_capabilities=frozenset(required_capabilities),
                )
                calls.append(plan)
                return plan

            def fake_plan_argv(plan):
                return ["podman", "run", "--rm", plan.image, *plan.run_spec.argv]

            agentd.create_run_execution_plan = fake_create_plan
            agentd.execution_plan_argv = fake_plan_argv
            captured_argv: list[list[str]] = []
            agentd.execute_runtime_plan = (
                lambda _cfg, _conn, _fileobj, plan:
                captured_argv.append(fake_plan_argv(plan)) or 0
            )
            agentd.lock_one = lambda *_args, **_kwargs: contextlib.nullcontext()

            cfg = {"images": {"codex": "codex-image", "cursor": "cursor-image"}}

            conn = FakeConn()
            assert agentd.op_run(
                cfg,
                conn,
                conn.fileobj,
                {"op": "run", "provider": "codex", "project": "project", "task": "task"},
            ) == 0
            assert frames(conn) == [
                {"type": "start", "interactive": True},
                {"type": "exit", "code": 0},
            ]
            assert calls[-1].readonly is False
            argv = captured_argv[-1]
            assert "--sandbox" in argv
            assert argv[argv.index("--sandbox") + 1] == "workspace-write"
            assert argv[-1] == "approval_policy=never"

            task_state["record"]["status"] = "completed"
            expect_request_error(
                "write execution is allowed only while task status is active",
                lambda: agentd.op_run(
                    cfg,
                    FakeConn(),
                    io.BytesIO(),
                    {"op": "run", "provider": "codex", "project": "project", "task": "task"},
                ),
            )

            readonly_conn = FakeConn()
            assert agentd.op_run(
                cfg,
                readonly_conn,
                readonly_conn.fileobj,
                {
                    "op": "run",
                    "provider": "codex",
                    "project": "project",
                    "task": "task",
                    "readonly": True,
                },
            ) == 0
            assert calls[-1].readonly is True
            argv = captured_argv[-1]
            assert argv[argv.index("--sandbox") + 1] == "read-only"

            task_state["record"]["status"] = "active"
            outer_conn = FakeConn()
            assert agentd.op_run(
                cfg,
                outer_conn,
                outer_conn.fileobj,
                {
                    "op": "run",
                    "provider": "codex",
                    "project": "project",
                    "task": "task",
                    "outer_only": True,
                    "prompt": "implement it",
                },
            ) == 0
            argv = captured_argv[-1]
            assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
            assert argv[-1] == "implement it"

            expect_request_error(
                "outer-only mode is Codex-only",
                lambda: agentd.op_run(
                    cfg,
                    FakeConn(),
                    io.BytesIO(),
                    {
                        "op": "run",
                        "provider": "cursor",
                        "project": "project",
                        "task": "task",
                        "outer_only": True,
                    },
                ),
            )
            expect_request_error(
                "unsupported provider",
                lambda: agentd.op_run(
                    cfg,
                    FakeConn(),
                    io.BytesIO(),
                    {
                        "op": "run",
                        "provider": "gemini",
                        "project": "project",
                        "task": "task",
                    },
                ),
            )
        finally:
            for name, value in originals.items():
                setattr(agentd, name, value)


def check_interactive_control_frames() -> None:
    originals = {
        "openpty": podman_runtime.pty.openpty,
        "popen": podman_runtime.subprocess.Popen,
        "podman_select": podman_runtime.select.select,
        "rpc_select": broker_runtime_io.select.select,
        "terminate": agentd.terminate_process_group,
        "cleanup": agentd.cleanup_interactive_container,
    }

    class FakeProc:
        def poll(self):
            return None

        def wait(self):
            raise AssertionError("wait must not be reached for cancel")

    def setup_frame(frame: dict):
        conn = FakeConn()
        fileobj = io.BytesIO(json.dumps(frame, separators=(",", ":")).encode() + b"\n")
        rfd, wfd = os.pipe()
        podman_runtime.pty.openpty = lambda: (rfd, wfd)
        podman_runtime.subprocess.Popen = lambda *_args, **_kwargs: FakeProc()
        podman_runtime.select.select = lambda *_args, **_kwargs: ([], [], [])
        broker_runtime_io.select.select = lambda *_args, **_kwargs: ([conn], [], [])
        agentd.terminate_process_group = lambda _proc: None
        agentd.cleanup_interactive_container = lambda _cidfile: None
        return conn, fileobj

    try:
        conn, fileobj = setup_frame({"type": "cancel"})
        assert agentd.stream_interactive(conn, fileobj, ["fake"]) == 130

        conn, fileobj = setup_frame({"type": "bogus"})
        expect_request_error(
            "unsupported interactive RPC frame",
            lambda: agentd.stream_interactive(conn, fileobj, ["fake"]),
        )
    finally:
        podman_runtime.pty.openpty = originals["openpty"]
        podman_runtime.subprocess.Popen = originals["popen"]
        podman_runtime.select.select = originals["podman_select"]
        broker_runtime_io.select.select = originals["rpc_select"]
        agentd.terminate_process_group = originals["terminate"]
        agentd.cleanup_interactive_container = originals["cleanup"]


def main() -> None:
    check_request_surface()
    check_controller_run_request()
    check_handle_framing()
    check_controller_error_codes()
    check_auth_contract()
    check_run_contract()
    check_interactive_control_frames()
    print("broker RPC compatibility contract regression checks passed")


if __name__ == "__main__":
    main()
