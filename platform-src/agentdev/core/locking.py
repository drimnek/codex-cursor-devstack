"""Provider-neutral filesystem locking and task lock selection."""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path
from typing import Iterator, Mapping

from agentdev.core.validation import valid_name

INTEGRATION_LOCK = "integration"


def lock_path(pp: Mapping[str, Path], lock_name: str) -> Path:
    """Return the lock-file path after applying the frozen lock-name validator."""
    lock_name = valid_name(lock_name, "lock")
    return Path(pp["runtime"]) / "locks" / f"{lock_name}.lock"


@contextlib.contextmanager
def lock_one(
    pp: Mapping[str, Path],
    lock_name: str,
    readonly: bool = False,
) -> Iterator[None]:
    """Acquire one shared/read-only or exclusive filesystem lock."""
    path = lock_path(pp, lock_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(
            handle.fileno(),
            fcntl.LOCK_SH if readonly else fcntl.LOCK_EX,
        )
        yield
    finally:
        handle.close()


def task_lock_name(task: str, record: Mapping[str, object]) -> str:
    """Select the workspace lock used by task completion and indexing."""
    task = valid_name(task, "task")
    return INTEGRATION_LOCK if record["mode"] == "integration" else task


def run_lock_name(task: str, record: Mapping[str, object]) -> str:
    """Select the workspace lock used by provider execution/review."""
    task = valid_name(task, "task")
    if record["mode"] == "integration" or record.get("status") == "merged":
        return INTEGRATION_LOCK
    return task
