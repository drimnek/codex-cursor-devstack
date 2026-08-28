"""Podman runtime backend for resolved agent execution plans."""
from __future__ import annotations

import fcntl
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
import uuid
from pathlib import Path
from typing import Callable

from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.execution.plan import ResolvedExecutionPlan
from agentdev.runtime.base import RuntimeBackend, RuntimeIO, RuntimeResult

LOG = logging.getLogger("agentd.runtime.podman")


def environment_args(environment: tuple[tuple[str, str], ...]) -> list[str]:
    args: list[str] = []
    for name, value in environment:
        args += ["-e", f"{name}={value}"]
    return args


def runtime_isolation_args(requirements: RuntimeIsolationRequirements) -> list[str]:
    """Translate provider-neutral outer-runtime isolation requirements."""
    if not isinstance(requirements, RuntimeIsolationRequirements):
        raise ValueError("runtime isolation must be RuntimeIsolationRequirements")
    args: list[str] = []
    if requirements.uid is not None:
        args += ["--user", f"{requirements.uid}:{requirements.gid}"]
    if requirements.nested_sandbox_bootstrap:
        args += ["--security-opt=unmask=/proc/*"]
    return args


def execution_plan_argv(plan: ResolvedExecutionPlan) -> list[str]:
    """Translate one fully resolved execution plan into Podman argv."""
    args = [
        "podman", "run", "--rm", f"--network={plan.network.mode}",
        f"--http-proxy={'true' if plan.network.http_proxy else 'false'}",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        f"--pids-limit={plan.resource_limits.pids}",
        f"--memory={plan.resource_limits.memory}",
        f"--cpus={plan.resource_limits.cpus}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
    ]
    args += runtime_isolation_args(plan.runtime_isolation)
    for mount in plan.all_mounts():
        args += [
            "-v",
            f"{mount.source}:{mount.target}:{'ro' if mount.read_only else 'rw'}",
        ]
    args += ["-w", plan.working_directory]
    args += environment_args(plan.environment)
    args += [plan.image, *plan.argv]
    return args


def run_noninteractive_argv(
    argv: list[str],
    io: RuntimeIO,
    *,
    cwd=None,
    env=None,
) -> RuntimeResult:
    LOG.info("exec noninteractive argv0=%s", argv[0])
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        io.write_output(chunk)
    return RuntimeResult(proc.wait())


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def terminate_process_group(
    proc: subprocess.Popen,
    *,
    int_grace: float = 2.0,
    term_grace: float = 3.0,
) -> None:
    """Terminate one interactive executor without affecting broker peers."""
    if proc.poll() is not None:
        return
    for sig, grace in ((signal.SIGINT, int_grace), (signal.SIGTERM, term_grace)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    proc.wait()


def new_interactive_cidfile(state_dir: Path) -> Path:
    root = Path(state_dir) / "interactive"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / f"{uuid.uuid4().hex}.cid"


def add_cidfile(argv: list[str], cidfile: Path) -> list[str]:
    if len(argv) < 2 or argv[:2] != ["podman", "run"]:
        raise ValueError("interactive executor must use podman run")
    return [*argv[:2], "--cidfile", str(cidfile), *argv[2:]]


def cleanup_interactive_container(cidfile: Path | None) -> None:
    if cidfile is None:
        return
    try:
        cid = cidfile.read_text().strip()
    except FileNotFoundError:
        cid = ""
    if cid:
        subprocess.run(
            ["podman", "rm", "-f", cid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    cidfile.unlink(missing_ok=True)


def run_interactive_argv(
    argv: list[str],
    io: RuntimeIO,
    *,
    cwd=None,
    env=None,
    timeout_seconds: float | None = None,
    timeout_output: bytes | None = None,
    cidfile: Path | None = None,
    terminate: Callable[..., None] = terminate_process_group,
    cleanup: Callable[[Path | None], None] = cleanup_interactive_container,
) -> RuntimeResult:
    LOG.info("exec interactive argv0=%s", argv[0])
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    cancelled = False
    timed_out = False
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                if timeout_output:
                    io.write_output(timeout_output)
                terminate(proc)
                break

            select_timeout = 0.25
            if deadline is not None:
                select_timeout = max(0.0, min(select_timeout, deadline - time.monotonic()))
            readable, _, _ = select.select([master], [], [], select_timeout)
            if master in readable:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if data:
                    io.write_output(data)

            control = io.receive_control(0.0)
            if control is not None:
                if control.kind == "input":
                    try:
                        os.write(master, control.data)
                    except OSError:
                        pass
                elif control.kind == "resize":
                    try:
                        assert control.rows is not None and control.cols is not None
                        set_winsize(master, control.rows, control.cols)
                    except Exception:
                        pass
                elif control.kind == "cancel":
                    cancelled = True
                    terminate(proc)
                    break

            if proc.poll() is not None:
                while True:
                    try:
                        data = os.read(master, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    io.write_output(data)
                break

        if timed_out:
            return RuntimeResult(124)
        if cancelled:
            return RuntimeResult(130)
        return RuntimeResult(proc.wait())
    finally:
        if proc.poll() is None:
            terminate(proc)
        cleanup(cidfile)
        os.close(master)


def run_podman_argv(
    argv: list[str],
    io: RuntimeIO,
    *,
    state_dir: Path,
    interactive: bool,
    cwd=None,
    env=None,
    timeout_seconds: float | None = None,
    timeout_output: bytes | None = None,
) -> RuntimeResult:
    """Execute raw Podman argv behind the runtime process-control boundary."""
    if type(interactive) is not bool:
        raise ValueError("interactive must be boolean")
    if not interactive:
        if timeout_seconds is not None:
            raise ValueError("noninteractive execution does not support timeout_seconds")
        return run_noninteractive_argv(argv, io, cwd=cwd, env=env)

    cidfile = new_interactive_cidfile(Path(state_dir))
    argv = add_cidfile(argv, cidfile)
    return run_interactive_argv(
        argv,
        io,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        timeout_output=timeout_output,
        cidfile=cidfile,
    )


class PodmanBackend(RuntimeBackend):
    """Execute resolved plans with the rootless Podman runtime."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)

    def id(self) -> str:
        return "podman"

    def execute(self, plan: ResolvedExecutionPlan, io: RuntimeIO) -> RuntimeResult:
        return run_podman_argv(
            execution_plan_argv(plan),
            io,
            state_dir=self._state_dir,
            interactive=plan.interaction_mode == "interactive",
        )
