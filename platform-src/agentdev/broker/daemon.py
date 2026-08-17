#!/usr/bin/python3
"""Constrained broker for rootless Podman agent executors and agent-owned Git state.

Runs as the dedicated unprivileged ``agentdev`` host account. Human-side tools
never execute Git commands in ``repo/agent`` after handoff; all operations on
that agent-controlled repository are mediated here.
"""
from __future__ import annotations

import base64
import datetime as dt
import fcntl
import json
import logging
import os
import pty
import select
import shutil
import signal
import socket
import struct
import subprocess
import termios
import time
import uuid
from pathlib import Path

from agentdev.core.dependencies import validate_dependencies as validate_task_dependencies
from agentdev.core.git_handoff import INTEGRATION_BRANCH
from agentdev.core.locking import (
    INTEGRATION_LOCK,
    lock_one,
    run_lock_name,
    task_lock_name,
)
from agentdev.core.projects import (
    export_agent_project,
    initialize_agent_project,
    project_git_status,
    resolve_project_paths,
    resolve_project_root,
    synchronize_agent_project,
)
from agentdev.core.validation import (
    InputValidationError,
    canonical_dir,
    canonical_file,
    ensure_under,
    valid_git_branch,
    valid_name,
)
from agentdev.core.tasks import (
    abort_task_locked,
    active_sequential,
    complete_task_locked,
    load_task as load_task_context,
    merge_task_locked,
    pending_parallel,
    prepare_task_start_request,
    prepare_task_start_target,
    start_task_locked,
    task_meta_path,
    task_records,
    validate_task_abort,
    validate_task_completion,
    validate_task_merge,
)
from agentdev.core.worktrees import BRANCH_PREFIX

CONFIG_PATH = Path("/srv/agent-dev/platform/config/platform.json")
ALLOWED_PROVIDERS = {"codex", "cursor"}
PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=false"
ALLOWED_OPS = {
    "ping", "build", "auth", "status", "versions", "smoke", "run", "index",
    "project-init", "project-sync", "project-export", "project-status",
    "task-start", "task-complete", "task-merge", "task-abort", "task-list",
}
AUTH_TIMEOUT_SECONDS = 900
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

logging.basicConfig(level=logging.INFO, format="agentd %(levelname)s %(message)s")
LOG = logging.getLogger("agentd")


# Compatibility name retained for the frozen broker RPC contract.
RequestError = InputValidationError


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


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

def project_root(cfg: dict, project: str) -> Path:
    return resolve_project_root(Path(cfg["root"]), project)


def project_paths(cfg: dict, project: str) -> dict[str, Path]:
    return resolve_project_paths(Path(cfg["root"]), project)

def read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"cannot read {what}: {path}: {exc}") from exc


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def git(repo: Path, *args: str, capture=False, check=True):
    argv = ["git", "-C", str(repo), *map(str, args)]
    LOG.info("git repo=%s command=%s", repo, args[0] if args else "")
    return subprocess.run(argv, check=check, text=True, capture_output=capture)


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args, capture=True).stdout.strip()


def ensure_git_repo(repo: Path, label: str) -> None:
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError(f"{label} is not a Git checkout: {repo}")


def ensure_clean(repo: Path, label: str) -> None:
    if git_text(repo, "status", "--porcelain"):
        raise ValueError(f"{label} has uncommitted or untracked changes")


def load_task(cfg: dict, project: str, task: str) -> tuple[dict, dict[str, Path], Path]:
    """Compatibility wrapper around the provider-neutral task loader."""
    return load_task_context(Path(cfg["root"]), project, task, read_json=read_json)


def ensure_volume(name: str) -> None:
    if subprocess.run(["podman", "volume", "exists", name]).returncode != 0:
        subprocess.run(["podman", "volume", "create", name], check=True)


def provider_volume(provider: str) -> str:
    return f"agent-dev-{provider}-state"


def cursor_auth_volume() -> str:
    return "agent-dev-cursor-auth"


def cursor_auth_target() -> str:
    return "/root/.config/cursor"


def legacy_provider_volume(provider: str) -> str:
    return f"agent-dev-{provider}-home"


def provider_state_dir(provider: str) -> str:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    return ".codex" if provider == "codex" else ".cursor"


def provider_state_target(provider: str) -> str:
    return f"/root/{provider_state_dir(provider)}"


def provider_seed_dir(cfg: dict, provider: str) -> Path:
    root = Path(cfg["root"])
    seed_root = canonical_dir(root / "platform" / "seed", root / "platform", "provider seed root")
    return canonical_dir(seed_root / provider, seed_root, f"{provider} policy directory")


def prepare_provider_state(cfg: dict, provider: str) -> None:
    """Create scoped provider state and migrate legacy whole-home state once."""
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    vol = provider_volume(provider)
    ensure_volume(vol)
    auth_vol = cursor_auth_volume() if provider == "cursor" else None
    if auth_vol is not None:
        ensure_volume(auth_vol)
    legacy = legacy_provider_volume(provider)
    legacy_exists = subprocess.run(
        ["podman", "volume", "exists", legacy],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    state_dir = provider_state_dir(provider)
    image = cfg["images"]["base"]
    argv = [
        "podman", "run", "--rm", "--network=none", "--http-proxy=false",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "-v", f"{vol}:/state:rw",
    ]
    if auth_vol is not None:
        argv += ["-v", f"{auth_vol}:/auth:rw"]
    if legacy_exists:
        argv += ["-v", f"{legacy}:/legacy:ro"]
    script = (
        "set -eu; "
        "marker=/state/.agent-dev-state-layout-v2; "
        "if [ ! -e \"$marker\" ]; then "
        "if find /state -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then "
        "echo 'provider state volume is non-empty but has no layout marker' >&2; exit 2; "
        "fi; "
        f"if [ -d /legacy/{state_dir} ]; then cp -a /legacy/{state_dir}/. /state/; fi; "
        + ("rm -f /state/config.toml; " if provider == "codex" else "")
        + ": > \"$marker\"; "
        "fi"
    )
    if provider == "cursor":
        script += (
            "; auth_marker=/auth/.agent-dev-auth-layout-v1; "
            "if [ ! -e \"$auth_marker\" ]; then "
            "if find /auth -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then "
            "echo 'Cursor auth state volume is non-empty but has no layout marker' >&2; exit 3; "
            "fi; "
            "if [ -d /legacy/.config/cursor ]; then cp -a /legacy/.config/cursor/. /auth/; fi; "
            ": > \"$auth_marker\"; "
            "fi"
        )
    argv += [image, "bash", "-lc", script]
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def seed_provider_home(cfg: dict, provider: str) -> None:
    """Prepare scoped provider state and reconcile platform-managed initial state."""
    prepare_provider_state(cfg, provider)
    if provider != "cursor":
        return
    vol = provider_volume(provider)
    image = cfg["images"]["base"]
    seed = provider_seed_dir(cfg, provider)
    config = canonical_file(seed / "cli-config.json", seed, "Cursor policy")
    argv = [
        "podman", "run", "--rm", "--network=none", "--http-proxy=false",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "-v", f"{vol}:/state:rw",
        "-v", f"{config}:/seed/cli-config.json:ro",
    ]
    script = (
        "set -eu; "
        "state=/state/cli-config.json; "
        "seed=/seed/cli-config.json; "
        "if [ ! -e \"$state\" ]; then "
        "install -m 0600 \"$seed\" \"$state\"; "
        "else "
        "jq -e '(.permissions | type) == \"object\"' \"$seed\" >/dev/null; "
        "tmp=$(mktemp /state/cli-config.json.tmp.XXXXXX); "
        "trap 'rm -f \"$tmp\"' EXIT; "
        "jq --slurpfile seed \"$seed\" '.permissions = $seed[0].permissions' \"$state\" > \"$tmp\"; "
        "chmod 0600 \"$tmp\"; "
        "mv -f \"$tmp\" \"$state\"; "
        "trap - EXIT; "
        "fi"
    )
    argv += [image, "bash", "-lc", script]
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def provider_policy_mounts(cfg: dict, provider: str) -> list[str]:
    # Codex policy is immutable at runtime. Cursor's CLI rewrites cli-config.json
    # atomically, so its active config stays writable while seed_provider_home()
    # reconciles platform-managed permissions before provider execution.
    if provider == "cursor":
        return []
    seed = provider_seed_dir(cfg, provider)
    config = canonical_file(seed / "config.toml", seed, "Codex policy")
    return ["-v", f"{config}:/root/.codex/config.toml:ro"]

def common_runtime_args(cfg: dict, provider: str | None, workspace: Path | None = None, *, readonly=False, reference: Path | None = None, task_meta: Path | None = None, git_common: Path | None = None, network_enabled: bool = True) -> list[str]:
    platform_root = Path(cfg["root"])
    network_mode = PROVIDER_NETWORK_MODE if provider is not None and network_enabled else "none"
    args = [
        "podman", "run", "--rm", f"--network={network_mode}", "--http-proxy=false", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        f"--pids-limit={cfg['limits']['pids']}", f"--memory={cfg['limits']['memory']}", f"--cpus={cfg['limits']['cpus']}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
    ]
    if provider is not None:
        if provider not in ALLOWED_PROVIDERS:
            raise ValueError("unsupported provider")
        args += ["-v", f"{provider_volume(provider)}:{provider_state_target(provider)}:rw"]
        if provider == "cursor":
            args += ["-v", f"{cursor_auth_volume()}:{cursor_auth_target()}:rw"]
        args += provider_policy_mounts(cfg, provider)
    if workspace is not None:
        workspace = canonical_dir(workspace, platform_root / "projects", "workspace mount")
        args += ["-v", f"{workspace}:/workspace:{'ro' if readonly else 'rw'}", "-w", "/workspace"]
    if reference is not None and reference.exists():
        reference = canonical_dir(reference, platform_root / "projects", "reference mount")
        args += ["-v", f"{reference}:/reference:ro"]
    if task_meta is not None:
        task_meta = canonical_file(task_meta, platform_root / "projects", "task metadata mount")
        args += ["-v", f"{task_meta}:/task/metadata.json:ro"]
    if git_common is not None:
        git_common = canonical_dir(git_common, platform_root / "projects", "Git common directory")
        if workspace is None:
            raise ValueError("Git common directory requires a workspace")
        mode = "ro" if readonly else "rw"
        args += ["-v", f"{workspace}:{workspace}:{mode}"]
        args += ["-v", f"{git_common}:{git_common}:{mode}"]
    return args

def stream_noninteractive(conn: socket.socket, argv: list[str], *, cwd=None, env=None) -> int:
    LOG.info("exec noninteractive argv0=%s", argv[0])
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        send_output(conn, chunk)
    return proc.wait()


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def terminate_process_group(proc: subprocess.Popen, *, int_grace: float = 2.0, term_grace: float = 3.0) -> None:
    """Terminate one interactive request without affecting unrelated broker work."""
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


def new_interactive_cidfile(cfg: dict) -> Path:
    root = Path(cfg["state_dir"]) / "interactive"
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
        subprocess.run(["podman", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    cidfile.unlink(missing_ok=True)


def stream_interactive(
    conn: socket.socket,
    fileobj,
    argv: list[str],
    *,
    cwd=None,
    env=None,
    timeout_seconds: float | None = None,
    cidfile: Path | None = None,
) -> int:
    LOG.info("exec interactive argv0=%s", argv[0])
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
        close_fds=True, start_new_session=True,
    )
    os.close(slave)
    cancelled = False
    timed_out = False
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                send_output(conn, b"\r\nAuthentication timed out.\r\n")
                terminate_process_group(proc)
                break

            select_timeout = 0.25
            if deadline is not None:
                select_timeout = max(0.0, min(select_timeout, deadline - time.monotonic()))
            readable, _, _ = select.select([master, conn], [], [], select_timeout)
            if master in readable:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if data:
                    send_output(conn, data)
            if conn in readable:
                msg = recv_json_line(fileobj)
                if msg is None:
                    cancelled = True
                    terminate_process_group(proc)
                    break
                if msg.get("type") == "input":
                    try:
                        os.write(master, base64.b64decode(msg.get("data", "")))
                    except OSError:
                        pass
                elif msg.get("type") == "resize":
                    try:
                        set_winsize(master, int(msg["rows"]), int(msg["cols"]))
                    except Exception:
                        pass
                elif msg.get("type") == "cancel":
                    cancelled = True
                    terminate_process_group(proc)
                    break
                else:
                    raise RequestError("unsupported interactive RPC frame")
            if proc.poll() is not None:
                while True:
                    try:
                        data = os.read(master, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    send_output(conn, data)
                break
        if timed_out:
            return 124
        if cancelled:
            return 130
        return proc.wait()
    finally:
        if proc.poll() is None:
            terminate_process_group(proc)
        cleanup_interactive_container(cidfile)
        os.close(master)


# ---- Git/project lifecycle operations (always agentdev) ---------------------

def op_project_init(cfg: dict, req: dict) -> dict:
    return initialize_agent_project(
        Path(cfg["root"]),
        req.get("project"),
        req.get("bundle"),
        read_json=read_json,
        git=git,
        git_text=git_text,
    )


def op_project_sync(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    bundle_name = valid_name(req.get("bundle"), "bundle")
    pp = project_paths(cfg, project)
    ensure_git_repo(pp["agent"], "repo/agent")
    records = task_records(pp)
    if active_sequential(records) or pending_parallel(records):
        raise RequestError("cannot sync while tasks are active or parallel tasks await merge")

    with lock_one(pp, INTEGRATION_LOCK, False):
        return synchronize_agent_project(
            Path(cfg["root"]),
            project,
            bundle_name,
            read_json=read_json,
            git=git,
            git_text=git_text,
        )


def op_project_export(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    pp = project_paths(cfg, project)
    ensure_git_repo(pp["agent"], "repo/agent")
    with lock_one(pp, INTEGRATION_LOCK, True):
        result = export_agent_project(
            Path(cfg["root"]),
            project,
            git=git,
            git_text=git_text,
        )
    # Named group ACL is needed because agentdev intentionally is not in ops_group.
    subprocess.run(["setfacl", "-m", f"g:{cfg['ops_group']}:r", result["bundle"]], check=True)
    return result

def validate_dependencies(pp: dict[str, Path], dependencies: list[str], base_commit: str) -> None:
    """Compatibility wrapper around provider-neutral dependency validation."""
    validate_task_dependencies(
        pp,
        dependencies,
        base_commit,
        read_json=read_json,
        git=git,
    )

def op_task_start(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    task, parallel, dependencies = prepare_task_start_request(
        req.get("task"),
        req.get("parallel", False),
        req.get("dependencies", []),
    )
    pp = project_paths(cfg, project)
    metadata_path = prepare_task_start_target(pp, task)
    with lock_one(pp, INTEGRATION_LOCK, False):
        return start_task_locked(
            pp,
            project,
            task,
            parallel,
            dependencies,
            metadata_path,
            records_reader=task_records,
            active_sequential_filter=active_sequential,
            pending_parallel_filter=pending_parallel,
            dependency_validator=validate_dependencies,
            write_json=write_json,
            git=git,
            git_text=git_text,
            now_iso=now_iso,
        )


def op_task_complete(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    rec, pp, ws = load_task(cfg, project, task)
    validate_task_completion(rec)
    lock_name = task_lock_name(task, rec)
    with lock_one(pp, lock_name, False):
        return complete_task_locked(
            pp,
            task,
            rec,
            ws,
            write_json=write_json,
            git_text=git_text,
            now_iso=now_iso,
        )


def op_task_merge(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    rec, pp, ws = load_task(cfg, project, task)
    validate_task_merge(rec)
    # Integration lock serializes merges; task lock prevents concurrent review/write.
    with lock_one(pp, INTEGRATION_LOCK, False):
        with lock_one(pp, task, False):
            return merge_task_locked(
                pp,
                task,
                rec,
                ws,
                write_json=write_json,
                git=git,
                git_text=git_text,
                now_iso=now_iso,
            )


def op_task_abort(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    rec, pp, ws = load_task(cfg, project, task)
    validate_task_abort(rec)
    with lock_one(pp, INTEGRATION_LOCK, False):
        with lock_one(pp, task, False):
            return abort_task_locked(
                pp,
                task,
                rec,
                ws,
                write_json=write_json,
                git=git,
                now_iso=now_iso,
            )


def op_task_list(cfg: dict, req: dict) -> list[dict]:
    project = valid_name(req.get("project"), "project")
    pp = project_paths(cfg, project)
    return task_records(pp)


def op_project_status(cfg: dict, req: dict) -> dict:
    project = valid_name(req.get("project"), "project")
    pp = project_paths(cfg, project)
    with lock_one(pp, INTEGRATION_LOCK, True):
        status = project_git_status(pp["agent"], git_text=git_text)
        return {"project": project, **status, "tasks": task_records(pp)}


# ---- Runtime/provider operations -------------------------------------------

def capture(argv: list[str]) -> str:
    proc = subprocess.run(argv, check=True, text=True, capture_output=True)
    return proc.stdout.strip()


def write_build_lock(cfg: dict) -> dict:
    images = {}
    for key in ("base", "codex", "cursor", "intelligence"):
        image = cfg["images"].get(key)
        if not image or subprocess.run(["podman", "image", "exists", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            continue
        images[key] = {
            "name": image,
            "image_id": capture(["podman", "image", "inspect", "--format", "{{.Id}}", image]),
        }
    tools = {
        "codex": capture(["podman", "run", "--rm", cfg["images"]["codex"], "codex", "--version"]),
        "cursor": capture(["podman", "run", "--rm", cfg["images"]["cursor"], "agent", "--version"]),
    }
    if "intelligence" in images:
        tools["gitnexus"] = capture(["podman", "run", "--rm", cfg["images"]["intelligence"], "gitnexus", "--version"])
    lock = {
        "platform": cfg["versions"]["platform"],
        "built_at": now_iso(),
        "images": images,
        "tools": tools,
    }
    path = Path(cfg["state_dir"]) / "build-manifest.lock.json"
    write_json(path, lock)
    return lock

def op_build(cfg: dict, conn: socket.socket) -> int:
    cdir = Path(cfg["root"]) / "platform" / "containers"
    core_commands = [
        ["podman", "build", "-f", cdir / "Containerfile.base", "-t", cfg["images"]["base"], cdir],
        ["podman", "build", "-f", cdir / "Containerfile.codex", "--build-arg", f"BASE_IMAGE={cfg['images']['base']}", "--build-arg", f"CODEX_VERSION={cfg['versions']['codex']}", "-t", cfg["images"]["codex"], cdir],
        ["podman", "build", "-f", cdir / "Containerfile.cursor", "--build-arg", f"BASE_IMAGE={cfg['images']['base']}", "-t", cfg["images"]["cursor"], cdir],
    ]
    for argv in core_commands:
        rc = stream_noninteractive(conn, list(map(str, argv)))
        if rc != 0:
            return rc
    seed_provider_home(cfg, "codex")
    seed_provider_home(cfg, "cursor")

    intelligence = cfg["images"].get("intelligence")
    if intelligence:
        argv = [
            "podman", "build", "-f", cdir / "Containerfile.intelligence",
            "--build-arg", f"BASE_IMAGE={cfg['images']['base']}",
            "--build-arg", f"GITNEXUS_VERSION={cfg['versions']['gitnexus']}",
            "-t", intelligence, cdir,
        ]
        rc = stream_noninteractive(conn, list(map(str, argv)))
        if rc != 0:
            send_output(conn, b"WARNING: optional GitNexus intelligence image failed to build; core executors are available.\n")
    lock = write_build_lock(cfg)
    send_output(conn, ("Build lock written: " + str(Path(cfg["state_dir"]) / "build-manifest.lock.json") + "\n").encode())
    LOG.info("build lock images=%s", sorted(lock["images"]))
    return 0

def op_auth(cfg: dict, conn: socket.socket, fileobj, provider: str) -> int:
    provider = valid_name(provider, "provider")
    if provider not in ALLOWED_PROVIDERS:
        raise RequestError("unsupported provider")
    seed_provider_home(cfg, provider)
    runtime = common_runtime_args(cfg, provider)
    if provider == "codex":
        argv = [*runtime, cfg["images"][provider], "codex", "login", "--device-auth"]
    else:
        argv = [*runtime, "-e", "NO_OPEN_BROWSER=1", cfg["images"][provider], "agent", "login"]
    cidfile = new_interactive_cidfile(cfg)
    argv = add_cidfile(argv, cidfile)
    send(conn, {"type": "start", "interactive": True})
    rc = stream_interactive(
        conn, fileobj, argv, timeout_seconds=AUTH_TIMEOUT_SECONDS, cidfile=cidfile
    )
    try:
        send(conn, {"type": "exit", "code": rc})
    except OSError:
        if rc != 130:
            raise
    return rc


def op_status(cfg: dict, conn: socket.socket) -> int:
    for provider in ("codex", "cursor"):
        seed_provider_home(cfg, provider)
        runtime = common_runtime_args(cfg, provider)
        argv = [*runtime, cfg["images"][provider]]
        argv += ["codex", "login", "status"] if provider == "codex" else ["agent", "status"]
        send_output(conn, f"\n== {provider} ==\n".encode())
        stream_noninteractive(conn, argv)
    return 0


def op_versions(cfg: dict, conn: socket.socket) -> int:
    for provider in ("codex", "cursor"):
        binary = "codex" if provider == "codex" else "agent"
        rc = stream_noninteractive(conn, ["podman", "run", "--rm", cfg["images"][provider], binary, "--version"])
        if rc != 0:
            return rc
    intelligence = cfg["images"].get("intelligence")
    if intelligence and subprocess.run(["podman", "image", "exists", intelligence]).returncode == 0:
        return stream_noninteractive(conn, ["podman", "run", "--rm", intelligence, "gitnexus", "--version"])
    send_output(conn, b"GitNexus intelligence image: not built (optional)\n")
    return 0

def op_smoke(cfg: dict, conn: socket.socket) -> int:
    image = cfg["images"]["base"]
    for key in ("base", "codex", "cursor"):
        if subprocess.run(["podman", "image", "exists", cfg["images"][key]]).returncode != 0:
            send_output(conn, f"missing image {cfg['images'][key]}\n".encode())
            return 2
    script = "set -eu; test ! -e /var/run/docker.sock; test ! -e /run/podman/podman.sock; test ! -e /root/.ssh/id_rsa; test ! -e /root/.aws/credentials; touch /tmp/ok"
    rc = stream_noninteractive(conn, ["podman", "run", "--rm", "--read-only", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", image, "bash", "-lc", script])
    if rc != 0:
        return rc
    for provider in ("codex", "cursor"):
        seed_provider_home(cfg, provider)
        runtime = common_runtime_args(cfg, provider, network_enabled=False)
        writable_targets = [
            (provider_state_target(provider), ".agent-dev-state-write-smoke"),
        ]
        if provider == "cursor":
            writable_targets.append((cursor_auth_target(), ".agent-dev-auth-write-smoke"))
        scoped_writes = "".join(
            f"touch {target}/{marker}; rm -f {target}/{marker}; "
            for target, marker in writable_targets
        )
        provider_script = (
            "set -eu; "
            "if touch /root/.agent-dev-unexpected-write 2>/dev/null; then "
            "rm -f /root/.agent-dev-unexpected-write; exit 41; "
            "fi; "
            + scoped_writes
        )
        rc = stream_noninteractive(
            conn,
            [*runtime, cfg["images"][provider], "bash", "-lc", provider_script],
        )
        if rc != 0:
            send_output(conn, f"{provider} scoped provider-state smoke failed\n".encode())
            return rc

    # Provider execution requires outbound networking, but host loopback access
    # is not part of the executor contract. Verify the explicit rootless network
    # backend starts, then ensure a listening host-loopback socket is unreachable.
    network_runtime = common_runtime_args(cfg, "codex")
    rc = stream_noninteractive(
        conn,
        [*network_runtime, cfg["images"]["codex"], "bash", "-lc", "true"],
    )
    if rc != 0:
        send_output(conn, b"provider network backend smoke failed\n")
        return rc
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host_port = listener.getsockname()[1]
        probe_script = (
            "timeout 3 bash -lc "
            + repr(f"exec 3<>/dev/tcp/host.containers.internal/{host_port}")
        )
        probe = subprocess.run(
            [*network_runtime, cfg["images"]["codex"], "bash", "-lc", probe_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            send_output(conn, b"provider network unexpectedly reached host loopback\n")
            return 4

    smoke = Path(cfg["root"]) / "tmp" / "smoke"
    shutil.rmtree(smoke, ignore_errors=True)
    smoke.mkdir(parents=True, exist_ok=True)
    marker = smoke / "marker"
    marker.write_text("safe\n")
    ro = subprocess.run(["podman", "run", "--rm", "--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "-v", f"{smoke}:/workspace:ro", image, "bash", "-lc", "echo bad >> /workspace/marker"])
    if ro.returncode == 0:
        send_output(conn, b"read-only mount test unexpectedly succeeded\n")
        return 3
    rc = stream_noninteractive(conn, ["podman", "run", "--rm", "--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "-v", f"{smoke}:/workspace:rw", image, "bash", "-lc", "echo ok >> /workspace/marker"])
    shutil.rmtree(smoke, ignore_errors=True)
    return rc


def op_index(cfg: dict, conn: socket.socket, req: dict) -> int:
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    rec, pp, ws = load_task(cfg, project, task)
    if rec.get("status") not in {"active", "completed"}:
        raise RequestError("index is allowed only for active/completed task workspaces")
    image = cfg["images"].get("intelligence")
    if not image or subprocess.run(["podman", "image", "exists", image]).returncode != 0:
        raise RequestError("optional GitNexus intelligence image is not available; rebuild or continue without indexing")
    meta = pp["tasks"] / f"{task}.json"
    git_common = pp["agent"] / ".git" if rec["mode"] == "parallel" and rec.get("status") != "merged" else None
    runtime = common_runtime_args(cfg, None, ws, readonly=False, reference=pp["reference"], task_meta=meta, git_common=git_common)
    argv = [*runtime, image, "gitnexus", "analyze", "--skip-agents-md", "--skip-skills", "--skip-embeddings"]
    lock_name = task_lock_name(task, rec)
    with lock_one(pp, lock_name, False):
        return stream_noninteractive(conn, argv)

def op_run(cfg: dict, conn: socket.socket, fileobj, req: dict) -> int:
    provider = req.get("provider")
    if provider not in ALLOWED_PROVIDERS:
        raise RequestError("unsupported provider")
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    readonly = bool(req.get("readonly", False))
    outer_only = bool(req.get("outer_only", False))
    prompt = req.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 100_000:
        raise RequestError("prompt must be a string <= 100000 characters")
    if outer_only and provider != "codex":
        raise RequestError("outer-only mode is Codex-only")
    rec, pp, ws = load_task(cfg, project, task)
    if not readonly and rec.get("status") != "active":
        raise RequestError("write execution is allowed only while task status is active")
    seed_provider_home(cfg, provider)
    meta = pp["tasks"] / f"{task}.json"
    git_common = pp["agent"] / ".git" if rec["mode"] == "parallel" and rec.get("status") != "merged" else None
    runtime = common_runtime_args(cfg, provider, ws, readonly=readonly, reference=pp["reference"], task_meta=meta, git_common=git_common)
    runtime += ["-e", f"AGENT_TASK_ID={task}", "-e", f"AGENT_TASK_MODE={rec['mode']}", "-e", f"AGENT_TASK_BASE_COMMIT={rec['base_commit']}"]
    image = cfg["images"][provider]
    if provider == "codex":
        if outer_only:
            agent_args = ["codex", "exec", "--sandbox", "danger-full-access", "-c", "approval_policy=never"]
        elif readonly:
            agent_args = ["codex", "exec", "--sandbox", "read-only", "-c", "approval_policy=never"]
        else:
            agent_args = ["codex", "exec", "--sandbox", "workspace-write", "-c", "approval_policy=never"]
        if prompt.strip():
            agent_args.append(prompt)
    else:
        # The broker has already validated and mounted this task workspace.
        # Cursor headless execution must not stop for a second trust prompt.
        agent_args = ["agent", "--trust"]
        if prompt.strip():
            agent_args.append(prompt)
    argv = [*runtime, image, *agent_args]
    lock_name = run_lock_name(task, rec)
    with lock_one(pp, lock_name, readonly):
        cidfile = new_interactive_cidfile(cfg)
        argv = add_cidfile(argv, cidfile)
        send(conn, {"type": "start", "interactive": True})
        rc = stream_interactive(conn, fileobj, argv, cidfile=cidfile)
        try:
            send(conn, {"type": "exit", "code": rc})
        except OSError:
            if rc != 130:
                raise
        return rc


def handle(conn: socket.socket, cfg: dict) -> None:
    fileobj = conn.makefile("rb")
    try:
        req = recv_json_line(fileobj)
        if req is None or not isinstance(req, dict):
            raise RequestError("invalid request")
        validate_request_shape(req)
        op = req["op"]
        try:
            peer_pid, peer_uid, peer_gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        except OSError:
            peer_pid = peer_uid = peer_gid = -1
        LOG.info("request uid=%s pid=%s op=%s project=%s task=%s provider=%s", peer_uid, peer_pid, op, req.get("project"), req.get("task"), req.get("provider"))

        result_ops = {
            "project-init": op_project_init,
            "project-sync": op_project_sync,
            "project-export": op_project_export,
            "project-status": op_project_status,
            "task-start": op_task_start,
            "task-complete": op_task_complete,
            "task-merge": op_task_merge,
            "task-abort": op_task_abort,
            "task-list": op_task_list,
        }
        if op == "ping":
            send(conn, {"type": "result", "result": {"status": "ok", "uid": os.getuid()}, "code": 0})
            return
        if op in result_ops:
            send(conn, {"type": "result", "result": result_ops[op](cfg, req), "code": 0})
            return
        if op in {"build", "status", "versions", "smoke", "index"}:
            send(conn, {"type": "start", "interactive": False})
            if op == "build": rc = op_build(cfg, conn)
            elif op == "status": rc = op_status(cfg, conn)
            elif op == "versions": rc = op_versions(cfg, conn)
            elif op == "smoke": rc = op_smoke(cfg, conn)
            else: rc = op_index(cfg, conn, req)
            send(conn, {"type": "exit", "code": rc})
            return
        if op == "auth":
            op_auth(cfg, conn, fileobj, req.get("provider"))
            return
        if op == "run":
            op_run(cfg, conn, fileobj, req)
            return
    except RequestError as exc:
        LOG.warning("request rejected: %s", exc)
        try:
            send(conn, {"type": "error", "message": str(exc), "code": 2})
        except OSError:
            pass
    except Exception:
        LOG.exception("request failed")
        try:
            send(conn, {"type": "error", "message": "internal broker error", "code": 1})
        except OSError:
            pass


def main() -> None:
    import threading
    cfg = load_config()
    listener = socket.socket(fileno=3)

    def serve(conn: socket.socket) -> None:
        try:
            handle(conn, cfg)
        finally:
            conn.close()

    while True:
        conn, _ = listener.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
