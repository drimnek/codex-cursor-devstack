"""Broker RPC framing, validation, dispatch, and socket serving boundary."""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Mapping

from agentdev.core.validation import InputValidationError


ALLOWED_OPS = {
    "ping", "build", "auth", "status", "versions", "smoke", "run", "index",
    "project-init", "project-sync", "project-export", "project-status",
    "task-start", "task-complete", "task-merge", "task-abort", "task-list",
}

REQUEST_FIELDS = {
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

RequestError = InputValidationError


@dataclass(frozen=True)
class BrokerOperations:
    """Operation callables consumed by the RPC dispatcher."""

    result_ops: Mapping[str, Callable[[dict, dict], object]]
    build: Callable[[dict, socket.socket], int]
    status: Callable[[dict, socket.socket], int]
    versions: Callable[[dict, socket.socket], int]
    smoke: Callable[[dict, socket.socket], int]
    index: Callable[[dict, socket.socket, dict], int]
    auth: Callable[[dict, socket.socket, object, str | None], int]
    run: Callable[[dict, socket.socket, object, dict], int]


def send(conn: socket.socket, obj: dict) -> None:
    conn.sendall(json.dumps(obj, separators=(",", ":")).encode() + b"\n")


def send_output(conn: socket.socket, data: bytes) -> None:
    if data:
        send(conn, {"type": "output", "data": base64.b64encode(data).decode()})


def recv_json_line(fileobj) -> dict | None:
    line = fileobj.readline()
    if not line:
        return None
    if len(line) > 1024 * 1024:
        raise RequestError("RPC frame too large")
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise RequestError("invalid JSON request") from exc


def validate_request_shape(req: dict) -> None:
    op = req.get("op")
    if op not in ALLOWED_OPS:
        raise RequestError("unsupported operation")
    allowed = REQUEST_FIELDS[op]
    unknown = set(req) - allowed
    if unknown:
        raise RequestError(f"unexpected RPC fields for {op}: {sorted(unknown)}")


def _peer_identity(conn: socket.socket) -> tuple[int, int, int]:
    try:
        return struct.unpack(
            "3i",
            conn.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            ),
        )
    except OSError:
        return -1, -1, -1


def handle_request(
    conn: socket.socket,
    cfg: dict,
    operations: BrokerOperations,
    *,
    logger: logging.Logger,
) -> None:
    """Decode one request, dispatch it, and emit the frozen v0.1 frame contract."""

    fileobj = conn.makefile("rb")
    try:
        req = recv_json_line(fileobj)
        if req is None or not isinstance(req, dict):
            raise RequestError("invalid request")
        validate_request_shape(req)
        op = req["op"]
        peer_pid, peer_uid, _peer_gid = _peer_identity(conn)
        logger.info(
            "request uid=%s pid=%s op=%s project=%s task=%s provider=%s",
            peer_uid,
            peer_pid,
            op,
            req.get("project"),
            req.get("task"),
            req.get("provider"),
        )

        if op == "ping":
            send(
                conn,
                {
                    "type": "result",
                    "result": {"status": "ok", "uid": os.getuid()},
                    "code": 0,
                },
            )
            return
        if op in operations.result_ops:
            send(
                conn,
                {
                    "type": "result",
                    "result": operations.result_ops[op](cfg, req),
                    "code": 0,
                },
            )
            return
        if op in {"build", "status", "versions", "smoke", "index"}:
            send(conn, {"type": "start", "interactive": False})
            if op == "build":
                rc = operations.build(cfg, conn)
            elif op == "status":
                rc = operations.status(cfg, conn)
            elif op == "versions":
                rc = operations.versions(cfg, conn)
            elif op == "smoke":
                rc = operations.smoke(cfg, conn)
            else:
                rc = operations.index(cfg, conn, req)
            send(conn, {"type": "exit", "code": rc})
            return
        if op == "auth":
            operations.auth(cfg, conn, fileobj, req.get("provider"))
            return
        if op == "run":
            operations.run(cfg, conn, fileobj, req)
            return
    except RequestError as exc:
        logger.warning("request rejected: %s", exc)
        try:
            send(conn, {"type": "error", "message": str(exc), "code": 2})
        except OSError:
            pass
    except Exception:
        logger.exception("request failed")
        try:
            send(conn, {"type": "error", "message": "internal broker error", "code": 1})
        except OSError:
            pass


def serve_fd3(cfg: dict, handler: Callable[[socket.socket, dict], None]) -> None:
    """Serve broker requests from the systemd-activated socket on file descriptor 3."""

    listener = socket.socket(fileno=3)

    def serve(conn: socket.socket) -> None:
        try:
            handler(conn, cfg)
        finally:
            conn.close()

    while True:
        conn, _ = listener.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
