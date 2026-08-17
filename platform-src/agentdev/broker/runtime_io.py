"""Broker RPC adapter for the provider-neutral runtime I/O contract."""
from __future__ import annotations

import base64
import select
import socket

from agentdev.broker.rpc import recv_json_line, send_output
from agentdev.core.validation import InputValidationError
from agentdev.runtime.base import RuntimeControl


class RpcRuntimeIO:
    """Normalize broker RPC frames into RuntimeControl events."""

    def __init__(self, conn: socket.socket, fileobj=None) -> None:
        self._conn = conn
        self._fileobj = fileobj

    def write_output(self, data: bytes) -> None:
        send_output(self._conn, data)

    def receive_control(self, timeout_seconds: float | None = None) -> RuntimeControl | None:
        if self._fileobj is None:
            return None
        readable, _, _ = select.select([self._conn], [], [], timeout_seconds)
        if self._conn not in readable:
            return None

        msg = recv_json_line(self._fileobj)
        if msg is None:
            return RuntimeControl("cancel")

        frame_type = msg.get("type")
        if frame_type == "input":
            return RuntimeControl("input", base64.b64decode(msg.get("data", "")))
        if frame_type == "resize":
            try:
                return RuntimeControl("resize", rows=int(msg["rows"]), cols=int(msg["cols"]))
            except (KeyError, TypeError, ValueError):
                return None
        if frame_type == "cancel":
            return RuntimeControl("cancel")
        raise InputValidationError("unsupported interactive RPC frame")
