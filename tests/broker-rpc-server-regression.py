#!/usr/bin/env python3
"""Regression coverage for the extracted broker RPC server boundary."""

from __future__ import annotations

import io
import json
import logging
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.broker import rpc  # noqa: E402
from agentdev.core.validation import InputValidationError  # noqa: E402


class FakeConn:
    def __init__(self, request: dict | None = None, raw: bytes | None = None):
        if raw is None:
            raw = b"" if request is None else json.dumps(request, separators=(",", ":")).encode() + b"\n"
        self.fileobj = io.BytesIO(raw)
        self.sent = bytearray()

    def makefile(self, _mode: str):
        return self.fileobj

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def getsockopt(self, _level, _option, _length):
        return struct.pack("3i", 1234, 1000, 1000)


def frames(conn: FakeConn) -> list[dict]:
    return [json.loads(line) for line in bytes(conn.sent).splitlines() if line]


def operations(calls: list[tuple]) -> rpc.BrokerOperations:
    def result_op(cfg: dict, req: dict):
        calls.append(("result", cfg, req))
        return {"ok": req["project"]}

    def simple(name: str, code: int):
        def invoke(cfg: dict, conn):
            calls.append((name, cfg, conn))
            return code
        return invoke

    def index(cfg: dict, conn, req: dict):
        calls.append(("index", cfg, conn, req))
        return 15

    def auth(cfg: dict, conn, fileobj, provider: str | None):
        calls.append(("auth", cfg, conn, fileobj, provider))
        rpc.send(conn, {"type": "start", "interactive": True})
        rpc.send(conn, {"type": "exit", "code": 16})
        return 16

    def run(cfg: dict, conn, fileobj, req: dict):
        calls.append(("run", cfg, conn, fileobj, req))
        rpc.send(conn, {"type": "start", "interactive": True})
        rpc.send(conn, {"type": "exit", "code": 17})
        return 17

    return rpc.BrokerOperations(
        result_ops={"project-status": result_op},
        build=simple("build", 11),
        status=simple("status", 12),
        versions=simple("versions", 13),
        smoke=simple("smoke", 14),
        index=index,
        auth=auth,
        run=run,
    )


def main() -> None:
    assert rpc.RequestError is InputValidationError

    calls: list[tuple] = []
    ops = operations(calls)
    logger = logging.getLogger("broker-rpc-server-regression")
    logger.disabled = True

    ping = FakeConn({"op": "ping"})
    rpc.handle_request(ping, {}, ops, logger=logger)
    ping_frames = frames(ping)
    assert len(ping_frames) == 1
    assert ping_frames[0]["type"] == "result"
    assert ping_frames[0]["code"] == 0
    assert ping_frames[0]["result"]["status"] == "ok"
    assert isinstance(ping_frames[0]["result"]["uid"], int)

    result = FakeConn({"op": "project-status", "project": "demo"})
    cfg = {"marker": "cfg"}
    rpc.handle_request(result, cfg, ops, logger=logger)
    assert frames(result) == [
        {"type": "result", "result": {"ok": "demo"}, "code": 0}
    ]
    assert calls[-1][0] == "result"

    status = FakeConn({"op": "status"})
    rpc.handle_request(status, cfg, ops, logger=logger)
    assert frames(status) == [
        {"type": "start", "interactive": False},
        {"type": "exit", "code": 12},
    ]

    index = FakeConn({"op": "index", "project": "demo", "task": "REQ-1"})
    rpc.handle_request(index, cfg, ops, logger=logger)
    assert frames(index) == [
        {"type": "start", "interactive": False},
        {"type": "exit", "code": 15},
    ]
    assert calls[-1][0] == "index"

    auth = FakeConn({"op": "auth", "provider": "codex"})
    rpc.handle_request(auth, cfg, ops, logger=logger)
    assert frames(auth) == [
        {"type": "start", "interactive": True},
        {"type": "exit", "code": 16},
    ]
    assert calls[-1][0] == "auth"
    assert calls[-1][-1] == "codex"

    run = FakeConn({
        "op": "run",
        "provider": "cursor",
        "project": "demo",
        "task": "REQ-1",
    })
    rpc.handle_request(run, cfg, ops, logger=logger)
    assert frames(run) == [
        {"type": "start", "interactive": True},
        {"type": "exit", "code": 17},
    ]
    assert calls[-1][0] == "run"

    invalid = FakeConn({"op": "ping", "extra": True})
    rpc.handle_request(invalid, cfg, ops, logger=logger)
    assert frames(invalid) == [
        {
            "type": "error",
            "message": "unexpected RPC fields for ping: ['extra']",
            "code": 2,
        }
    ]

    bad_json = FakeConn(raw=b"{not-json}\n")
    rpc.handle_request(bad_json, cfg, ops, logger=logger)
    assert frames(bad_json) == [
        {"type": "error", "message": "invalid JSON request", "code": 2}
    ]

    too_large = FakeConn(raw=b"{" + b"x" * (1024 * 1024) + b"}\n")
    rpc.handle_request(too_large, cfg, ops, logger=logger)
    assert frames(too_large) == [
        {"type": "error", "message": "RPC frame too large", "code": 2}
    ]

    def explode(_cfg: dict, _req: dict):
        raise RuntimeError("private internal detail")

    broken_ops = rpc.BrokerOperations(
        result_ops={"project-status": explode},
        build=ops.build,
        status=ops.status,
        versions=ops.versions,
        smoke=ops.smoke,
        index=ops.index,
        auth=ops.auth,
        run=ops.run,
    )
    internal = FakeConn({"op": "project-status", "project": "demo"})
    rpc.handle_request(internal, cfg, broken_ops, logger=logger)
    assert frames(internal) == [
        {"type": "error", "message": "internal broker error", "code": 1}
    ]

    output = FakeConn()
    rpc.send_output(output, b"abc")
    assert frames(output) == [{"type": "output", "data": "YWJj"}]

    daemon_source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    rpc_source = (PLATFORM / "agentdev/broker/rpc.py").read_text(encoding="utf-8")
    assert 'conn.makefile("rb")' not in daemon_source
    assert "socket.SO_PEERCRED" not in daemon_source
    assert "socket.socket(fileno=3)" not in daemon_source
    assert "threading.Thread" not in daemon_source
    assert "REQUEST_FIELDS =" not in daemon_source
    assert "ALLOWED_OPS =" not in daemon_source
    assert 'conn.makefile("rb")' in rpc_source
    assert "socket.SO_PEERCRED" in rpc_source
    assert "socket.socket(fileno=3)" in rpc_source
    assert "threading.Thread" in rpc_source

    print("broker RPC server regression checks passed")


if __name__ == "__main__":
    main()
