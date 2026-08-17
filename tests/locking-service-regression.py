#!/usr/bin/env python3
"""Freeze the provider-neutral locking service extracted by MA2-CORE-005."""
from __future__ import annotations

import fcntl
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.core.locking import (  # noqa: E402
    INTEGRATION_LOCK,
    lock_one,
    lock_path,
    run_lock_name,
    task_lock_name,
)
from agentdev.core.validation import InputValidationError  # noqa: E402


def expect_invalid_lock_name(pp: dict[str, Path], value: object) -> None:
    try:
        lock_path(pp, value)  # type: ignore[arg-type]
    except InputValidationError:
        return
    raise AssertionError(f"invalid lock name was accepted: {value!r}")


def assert_nonblocking_conflict(path: Path, operation: int) -> None:
    with path.open("a+") as other:
        try:
            fcntl.flock(other.fileno(), operation | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        else:
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)
    raise AssertionError("conflicting lock unexpectedly succeeded")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        runtime = Path(td) / "runtime"
        pp = {"runtime": runtime}

        expected = runtime / "locks" / "REQ-1.lock"
        assert lock_path(pp, "REQ-1") == expected
        assert not expected.parent.exists(), "lock path calculation must not create directories"

        for invalid in (None, "", "../escape", "bad/name", "x" * 65):
            expect_invalid_lock_name(pp, invalid)

        with lock_one(pp, "REQ-1", readonly=True):
            assert expected.is_file()
            with expected.open("a+") as peer:
                fcntl.flock(peer.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(peer.fileno(), fcntl.LOCK_UN)
            assert_nonblocking_conflict(expected, fcntl.LOCK_EX)

        with lock_one(pp, "REQ-1", readonly=False):
            assert_nonblocking_conflict(expected, fcntl.LOCK_SH)
            assert_nonblocking_conflict(expected, fcntl.LOCK_EX)

    assert task_lock_name("REQ-1", {"mode": "integration"}) == INTEGRATION_LOCK
    assert task_lock_name("REQ-1", {"mode": "parallel"}) == "REQ-1"
    assert run_lock_name("REQ-1", {"mode": "integration", "status": "active"}) == INTEGRATION_LOCK
    assert run_lock_name("REQ-1", {"mode": "parallel", "status": "active"}) == "REQ-1"
    assert run_lock_name("REQ-1", {"mode": "parallel", "status": "completed"}) == "REQ-1"
    assert run_lock_name("REQ-1", {"mode": "parallel", "status": "merged"}) == INTEGRATION_LOCK

    daemon_source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    locking_source = (PLATFORM / "agentdev/core/locking.py").read_text(encoding="utf-8")
    assert "fcntl.flock" not in daemon_source
    assert "fcntl.flock" in locking_source
    assert "def lock_one(" not in daemon_source
    assert "def lock_one(" in locking_source
    assert 'lock_name = "integration" if rec["mode"]' not in daemon_source
    assert "codex" not in locking_source.lower()
    assert "cursor" not in locking_source.lower()
    assert "podman" not in locking_source.lower()

    print("locking service regression checks passed")


if __name__ == "__main__":
    main()
