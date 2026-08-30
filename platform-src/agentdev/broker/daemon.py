#!/usr/bin/python3
"""Constrained broker for rootless Podman agent executors and agent-owned Git state.

Runs as the dedicated unprivileged ``agentdev`` host account. Human-side tools
never execute Git commands in ``repo/agent`` after handoff; all operations on
that agent-controlled repository are mediated here.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
from pathlib import Path, PurePosixPath

from agentdev.agents.base import RunSpec
from agentdev.agents.registry import BUILTIN_AGENT_REGISTRY, UnknownAgentError
from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.execution.plan import (
    ExecutionMount,
    NetworkRuntimeRequirements,
    ResolvedExecutionPlan,
    ResolvedProviderPolicyArtifacts,
    ResourceLimits,
)

from agentdev.broker.runtime_io import RpcRuntimeIO
from agentdev.broker.rpc import (
    ALLOWED_OPS,
    REQUEST_FIELDS,
    BrokerOperations,
    handle_request,
    recv_json_line,
    send,
    send_output,
    serve_fd3,
    validate_request_shape,
)
from agentdev.core.dependencies import validate_dependencies as validate_task_dependencies
from agentdev.core.git_handoff import INTEGRATION_BRANCH
from agentdev.core.models import TaskContext
from agentdev.policy.capabilities import MissingCapabilitiesError, require_capabilities
from agentdev.policy.legacy import resolve_run_profile_request
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
from agentdev.runtime.podman import (
    PodmanBackend,
    add_cidfile,
    cleanup_interactive_container,
    environment_args as provider_environment_args,
    execution_plan_argv,
    runtime_isolation_args,
    new_interactive_cidfile as podman_new_interactive_cidfile,
    run_interactive_argv,
    run_noninteractive_argv,
    run_podman_argv,
    terminate_process_group,
)

CONFIG_PATH = Path("/srv/agent-dev/platform/config/platform.json")
AGENT_REGISTRY = BUILTIN_AGENT_REGISTRY
PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=false"
AUTH_TIMEOUT_SECONDS = 900

logging.basicConfig(level=logging.INFO, format="agentd %(levelname)s %(message)s")
LOG = logging.getLogger("agentd")


# Compatibility name retained for the frozen broker RPC contract.
RequestError = InputValidationError


def registered_provider(provider: object, *, request_error: bool = False):
    """Resolve a trusted driver while preserving frozen broker error messages."""
    try:
        return AGENT_REGISTRY.get(provider)
    except UnknownAgentError as exc:
        error_type = RequestError if request_error else ValueError
        raise error_type("unsupported provider") from exc


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


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


def provider_state_adapter(provider: str):
    return registered_provider(provider).state_adapter()


def provider_volume(provider: str) -> str:
    """Compatibility accessor for the provider's primary scoped state volume."""
    return provider_state_adapter(provider).primary().mount.source


def cursor_auth_volume() -> str:
    """Compatibility accessor retained for the frozen state-layout regression."""
    return provider_state_adapter("cursor").volume("auth").mount.source


def cursor_auth_target() -> str:
    """Compatibility accessor retained for the frozen state-layout regression."""
    return provider_state_adapter("cursor").volume("auth").mount.target


def legacy_provider_volume(provider: str) -> str:
    legacy = provider_state_adapter(provider).legacy_volume
    if legacy is None:
        raise ValueError("provider has no legacy state volume")
    return legacy


def provider_state_dir(provider: str) -> str:
    """Compatibility accessor derived from driver-owned state metadata."""
    return PurePosixPath(provider_state_adapter(provider).primary().mount.target).name


def provider_state_target(provider: str) -> str:
    return provider_state_adapter(provider).primary().mount.target


def provider_state_smoke_isolation(provider: str) -> RuntimeIsolationRequirements | None:
    """Use the declared writable-state owner when probing provider state writes."""
    writable = tuple(
        layout
        for layout in provider_state_adapter(provider).volumes
        if not layout.mount.read_only
    )
    owners = {(layout.owner_uid, layout.owner_gid) for layout in writable}
    if owners == {(None, None)} or not owners:
        return None
    if len(owners) != 1 or any(value is None for value in next(iter(owners))):
        raise ValueError("writable provider state must declare one consistent runtime owner")
    uid, gid = next(iter(owners))
    return RuntimeIsolationRequirements(uid=uid, gid=gid)


def provider_seed_dir(cfg: dict, provider: str) -> Path:
    registered_provider(provider)
    root = Path(cfg["root"])
    seed_root = canonical_dir(root / "platform" / "seed", root / "platform", "provider seed root")
    return canonical_dir(seed_root / provider, seed_root, f"{provider} policy directory")


def _migration_script(adapter) -> str:
    parts = ["set -eu"]
    for index, layout in enumerate(adapter.volumes):
        marker_var = "marker" if index == 0 else f"{layout.key}_marker"
        marker_path = f"{layout.staging_target}/{layout.marker}"
        parts.append(f"{marker_var}={shlex.quote(marker_path)}")
        body = [
            f"if find {shlex.quote(layout.staging_target)} -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then "
            f"echo {shlex.quote(layout.empty_error)} >&2; exit {2 + index}; fi"
        ]
        if layout.legacy_path is not None:
            legacy_path = f"/legacy/{layout.legacy_path}"
            body.append(
                f"if [ -d {shlex.quote(legacy_path)} ]; then "
                f"cp -a {shlex.quote(legacy_path)}/. {shlex.quote(layout.staging_target)}/; fi"
            )
        for relative in layout.cleanup_after_copy:
            body.append(
                f"rm -f {shlex.quote(layout.staging_target + '/' + relative)}"
            )
        body.append(f': > "${marker_var}"')
        parts.append(
            f'if [ ! -e "${marker_var}" ]; then ' + "; ".join(body) + "; fi"
        )
    return "; ".join(parts)


def _provider_state_layout_ready(image: str, layout) -> bool:
    """Check an existing layout marker using the declared state owner."""
    argv = [
        "podman", "run", "--rm", "--network=none", "--http-proxy=false",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
    ]
    if layout.owner_uid is not None:
        argv += runtime_isolation_args(
            RuntimeIsolationRequirements(uid=layout.owner_uid, gid=layout.owner_gid)
        )
    marker = f"{layout.staging_target}/{layout.marker}"
    argv += [
        "-v", f"{layout.mount.source}:{layout.staging_target}:ro",
        image, "bash", "-lc", f"test -e {shlex.quote(marker)}",
    ]
    return subprocess.run(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def prepare_provider_state(cfg: dict, provider: str) -> None:
    """Create driver-declared scoped state and migrate legacy state once."""
    adapter = provider_state_adapter(provider)
    for layout in adapter.volumes:
        ensure_volume(layout.mount.source)

    legacy = adapter.legacy_volume
    legacy_exists = bool(legacy) and subprocess.run(
        ["podman", "volume", "exists", legacy],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0

    image = cfg["images"]["base"]
    if not all(_provider_state_layout_ready(image, layout) for layout in adapter.volumes):
        argv = [
            "podman", "run", "--rm", "--network=none", "--http-proxy=false",
            "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        ]
        for layout in adapter.volumes:
            argv += ["-v", f"{layout.mount.source}:{layout.staging_target}:rw"]
        if legacy_exists:
            argv += ["-v", f"{legacy}:/legacy:ro"]
        argv += [image, "bash", "-lc", _migration_script(adapter)]
        subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for layout in adapter.volumes:
        if layout.owner_uid is None:
            continue
        owner = f"{layout.owner_uid}:{layout.owner_gid}"
        marker = f"{layout.staging_target}/.agent-dev-owner-{layout.owner_uid}-{layout.owner_gid}"
        probe = [
            "podman", "run", "--rm", "--network=none", "--http-proxy=false",
            "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
            *runtime_isolation_args(
                RuntimeIsolationRequirements(uid=layout.owner_uid, gid=layout.owner_gid)
            ),
            "-v", f"{layout.mount.source}:{layout.staging_target}:ro",
            image, "bash", "-lc",
            f"test \"$(stat -c '%u:%g' {shlex.quote(layout.staging_target)})\" = {owner} "
            f"&& test -e {shlex.quote(marker)}",
        ]
        if subprocess.run(
            probe,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0:
            continue
        adjust = [
            "podman", "run", "--rm", "--network=none", "--http-proxy=false",
            "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--user", owner,
            "-v", f"{layout.mount.source}:{layout.staging_target}:rw,U",
            image, "bash", "-lc",
            f"set -eu; test \"$(stat -c '%u:%g' {shlex.quote(layout.staging_target)})\" = {owner}; "
            f": > {shlex.quote(marker)}",
        ]
        subprocess.run(
            adjust,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def seed_provider_home(cfg: dict, provider: str) -> None:
    """Prepare driver-declared state and reconcile platform-managed fields."""
    prepare_provider_state(cfg, provider)
    adapter = provider_state_adapter(provider)
    plan = adapter.reconciliation
    if plan is None:
        return

    layout = adapter.volume(plan.volume_key)
    image = cfg["images"]["base"]
    seed = provider_seed_dir(cfg, provider)
    config = canonical_file(seed / plan.seed_relative_path, seed, f"{provider} policy")
    seed_target = f"/seed/{plan.seed_relative_path}"
    state_target = f"/state/{plan.state_relative_path}"
    field = plan.managed_field
    argv = [
        "podman", "run", "--rm", "--network=none", "--http-proxy=false",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
    ]
    if layout.owner_uid is not None:
        argv += runtime_isolation_args(
            RuntimeIsolationRequirements(uid=layout.owner_uid, gid=layout.owner_gid)
        )
    argv += [
        "-v", f"{layout.mount.source}:/state:rw",
        "-v", f"{config}:{seed_target}:ro",
    ]
    script = (
        "set -eu; "
        f"state={shlex.quote(state_target)}; "
        f"seed={shlex.quote(seed_target)}; "
        'if [ ! -e "$state" ]; then '
        'install -m 0600 "$seed" "$state"; '
        "else "
        f"jq -e '(.{field} | type) == \"object\"' \"$seed\" >/dev/null; "
        f"tmp=$(mktemp /state/{PurePosixPath(plan.state_relative_path).name}.tmp.XXXXXX); "
        "trap 'rm -f \"$tmp\"' EXIT; "
        f"jq --slurpfile seed \"$seed\" '.{field} = $seed[0].{field}' \"$state\" > \"$tmp\"; "
        'chmod 0600 "$tmp"; '
        'mv -f "$tmp" "$state"; '
        "trap - EXIT; "
        "fi"
    )
    argv += [image, "bash", "-lc", script]
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def provider_policy_mounts(cfg: dict, provider: str) -> list[str]:
    adapter = provider_state_adapter(provider)
    if not adapter.policy_mounts:
        return []
    seed = provider_seed_dir(cfg, provider)
    mounts: list[str] = []
    for policy in adapter.policy_mounts:
        source = canonical_file(
            seed / policy.seed_relative_path,
            seed,
            f"{provider} policy",
        )
        mounts += [
            "-v",
            f"{source}:{policy.target}:{'ro' if policy.read_only else 'rw'}",
        ]
    return mounts

def common_runtime_args(cfg: dict, provider: str | None, workspace: Path | None = None, *, readonly=False, reference: Path | None = None, task_meta: Path | None = None, git_common: Path | None = None, network_enabled: bool = True, runtime_isolation=None) -> list[str]:
    platform_root = Path(cfg["root"])
    network_mode = PROVIDER_NETWORK_MODE if provider is not None and network_enabled else "none"
    args = [
        "podman", "run", "--rm", f"--network={network_mode}", "--http-proxy=false", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        f"--pids-limit={cfg['limits']['pids']}", f"--memory={cfg['limits']['memory']}", f"--cpus={cfg['limits']['cpus']}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
    ]
    if runtime_isolation is not None:
        args += runtime_isolation_args(runtime_isolation)
    if provider is not None:
        driver = registered_provider(provider)
        for state in driver.state_spec():
            args += [
                "-v",
                f"{state.source}:{state.target}:{'ro' if state.read_only else 'rw'}",
            ]
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

def _merge_plan_environment(*groups: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for name, value in group:
            if name in seen:
                raise ValueError(f"duplicate resolved execution environment key {name!r}")
            seen.add(name)
            merged.append((name, value))
    return tuple(merged)


def create_run_execution_plan(
    cfg: dict,
    provider: str,
    context: TaskContext,
    run_spec: RunSpec,
    *,
    readonly: bool,
    outer_only: bool,
    reference: Path,
    git_common: Path | None,
) -> ResolvedExecutionPlan:
    """Resolve authorized broker/task/provider inputs into one executor plan."""
    platform_root = Path(cfg["root"])
    driver = registered_provider(provider)
    workspace = canonical_dir(context.workspace, platform_root / "projects", "workspace mount")
    task_meta = canonical_file(context.metadata_path, platform_root / "projects", "task metadata mount")

    references: tuple[ExecutionMount, ...] = ()
    if reference.exists():
        reference = canonical_dir(reference, platform_root / "projects", "reference mount")
        references = (ExecutionMount(str(reference), "/reference", True, "reference"),)

    state_mounts = tuple(
        ExecutionMount(state.source, state.target, state.read_only, "provider-state")
        for state in driver.state_spec()
    )

    policy_mounts: list[ExecutionMount] = [
        ExecutionMount(item.source, item.target, item.read_only, "provider-policy")
        for item in run_spec.policy_artifacts.files
    ]
    adapter = provider_state_adapter(provider)
    if adapter.policy_mounts:
        seed = provider_seed_dir(cfg, provider)
        for policy_mount in adapter.policy_mounts:
            source = canonical_file(
                seed / policy_mount.seed_relative_path,
                seed,
                f"{provider} policy",
            )
            policy_mounts.append(
                ExecutionMount(
                    str(source),
                    policy_mount.target,
                    policy_mount.read_only,
                    "provider-policy",
                )
            )

    auxiliary: list[ExecutionMount] = []
    if git_common is not None:
        git_common = canonical_dir(git_common, platform_root / "projects", "Git common directory")
        mode_read_only = readonly
        auxiliary += [
            ExecutionMount(str(workspace), str(workspace), mode_read_only, "git-worktree"),
            ExecutionMount(str(git_common), str(git_common), mode_read_only, "git-common"),
        ]

    task_environment = (
        ("AGENT_TASK_ID", context.task),
        ("AGENT_TASK_MODE", context.mode),
        ("AGENT_TASK_BASE_COMMIT", str(context.record["base_commit"])),
    )
    environment = _merge_plan_environment(
        task_environment,
        run_spec.environment,
        run_spec.policy_artifacts.environment,
    )
    required_capabilities = {"workspace:readonly" if readonly else "workspace:writable"}
    if run_spec.interactive:
        required_capabilities.add("interactive-run")
    if outer_only:
        required_capabilities.add("compatibility:outer-only")

    return ResolvedExecutionPlan(
        agent_id=provider,
        image=cfg["images"][provider],
        argv=run_spec.argv,
        environment=environment,
        workspace_mount=ExecutionMount(str(workspace), "/workspace", readonly, "workspace"),
        reference_mounts=references,
        task_metadata_mount=ExecutionMount(
            str(task_meta), "/task/metadata.json", True, "task-metadata"
        ),
        provider_state_mounts=state_mounts,
        provider_policy_artifacts=ResolvedProviderPolicyArtifacts(
            mounts=tuple(policy_mounts),
            argv=run_spec.policy_artifacts.argv,
            environment=run_spec.policy_artifacts.environment,
        ),
        resource_limits=ResourceLimits(
            pids=cfg["limits"]["pids"],
            memory=cfg["limits"]["memory"],
            cpus=cfg["limits"]["cpus"],
        ),
        network=NetworkRuntimeRequirements(PROVIDER_NETWORK_MODE, http_proxy=False),
        runtime_isolation=run_spec.runtime_isolation,
        readonly=readonly,
        interaction_mode="interactive" if run_spec.interactive else "noninteractive",
        security_class=None,
        required_capabilities=frozenset(required_capabilities),
        auxiliary_mounts=tuple(auxiliary),
    )


def stream_noninteractive(conn: socket.socket, argv: list[str], *, cwd=None, env=None) -> int:
    """Compatibility wrapper around runtime-owned noninteractive process execution."""
    result = run_noninteractive_argv(argv, RpcRuntimeIO(conn), cwd=cwd, env=env)
    return result.exit_code


def new_interactive_cidfile(cfg: dict) -> Path:
    """Compatibility wrapper around the runtime-owned cidfile allocator."""
    return podman_new_interactive_cidfile(Path(cfg["state_dir"]))


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
    """Compatibility wrapper around runtime-owned PTY/process execution."""
    result = run_interactive_argv(
        argv,
        RpcRuntimeIO(conn, fileobj),
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        timeout_output=b"\r\nAuthentication timed out.\r\n",
        cidfile=cidfile,
        terminate=terminate_process_group,
        cleanup=cleanup_interactive_container,
    )
    return result.exit_code


def execute_runtime_argv(
    cfg: dict,
    conn: socket.socket,
    fileobj,
    argv: list[str],
    *,
    interactive: bool,
    timeout_seconds: float | None = None,
    timeout_output: bytes | None = None,
) -> int:
    """Execute raw Podman argv without exposing process-control mechanics."""
    runtime_io = RpcRuntimeIO(conn, fileobj if interactive else None)
    return run_podman_argv(
        argv,
        runtime_io,
        state_dir=Path(cfg["state_dir"]),
        interactive=interactive,
        timeout_seconds=timeout_seconds,
        timeout_output=timeout_output,
    ).exit_code


def execute_runtime_plan(
    cfg: dict,
    conn: socket.socket,
    fileobj,
    plan: ResolvedExecutionPlan,
) -> int:
    """Execute one resolved plan through the configured runtime backend."""
    backend = PodmanBackend(Path(cfg["state_dir"]))
    return backend.execute(plan, RpcRuntimeIO(conn, fileobj)).exit_code


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


def provider_version_argv(provider: str) -> tuple[str, ...]:
    return registered_provider(provider).version_probe().argv


def write_build_lock(cfg: dict) -> dict:
    images = {}
    for key in ("base", *AGENT_REGISTRY.ids(), "intelligence"):
        image = cfg["images"].get(key)
        if not image or subprocess.run(["podman", "image", "exists", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            continue
        images[key] = {
            "name": image,
            "image_id": capture(["podman", "image", "inspect", "--format", "{{.Id}}", image]),
        }
    tools = {
        provider: capture(["podman", "run", "--rm", cfg["images"][provider], *provider_version_argv(provider)])
        for provider in AGENT_REGISTRY.ids()
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
    for provider in AGENT_REGISTRY.ids():
        seed_provider_home(cfg, provider)

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
    driver = registered_provider(provider, request_error=True)
    seed_provider_home(cfg, provider)
    spec = driver.auth_spec()
    runtime = common_runtime_args(
        cfg, provider, runtime_isolation=spec.runtime_isolation
    )
    env_args = provider_environment_args(spec.environment)
    agent_args = list(spec.argv)
    argv = [*runtime, *env_args, cfg["images"][provider], *agent_args]
    send(conn, {"type": "start", "interactive": spec.interactive})
    rc = execute_runtime_argv(
        cfg,
        conn,
        fileobj,
        argv,
        interactive=spec.interactive,
        timeout_seconds=spec.timeout_seconds,
        timeout_output=b"\r\nAuthentication timed out.\r\n",
    )
    try:
        send(conn, {"type": "exit", "code": rc})
    except OSError:
        if rc != 130:
            raise
    return rc


def op_status(cfg: dict, conn: socket.socket) -> int:
    for provider in AGENT_REGISTRY.ids():
        driver = registered_provider(provider)
        seed_provider_home(cfg, provider)
        spec = driver.auth_status_spec()
        runtime = common_runtime_args(
            cfg, provider, runtime_isolation=spec.runtime_isolation
        )
        env_args = provider_environment_args(spec.environment)
        agent_args = list(spec.argv)
        argv = [*runtime, *env_args, cfg["images"][provider], *agent_args]
        send_output(conn, f"\n== {provider} ==\n".encode())
        stream_noninteractive(conn, argv)
    return 0


def op_versions(cfg: dict, conn: socket.socket) -> int:
    for provider in AGENT_REGISTRY.ids():
        rc = stream_noninteractive(
            conn,
            ["podman", "run", "--rm", cfg["images"][provider], *provider_version_argv(provider)],
        )
        if rc != 0:
            return rc
    intelligence = cfg["images"].get("intelligence")
    if intelligence and subprocess.run(["podman", "image", "exists", intelligence]).returncode == 0:
        return stream_noninteractive(conn, ["podman", "run", "--rm", intelligence, "gitnexus", "--version"])
    send_output(conn, b"GitNexus intelligence image: not built (optional)\n")
    return 0

def op_smoke(cfg: dict, conn: socket.socket) -> int:
    image = cfg["images"]["base"]
    for key in ("base", *AGENT_REGISTRY.ids()):
        if subprocess.run(["podman", "image", "exists", cfg["images"][key]]).returncode != 0:
            send_output(conn, f"missing image {cfg['images'][key]}\n".encode())
            return 2
    script = "set -eu; test ! -e /var/run/docker.sock; test ! -e /run/podman/podman.sock; test ! -e /root/.ssh/id_rsa; test ! -e /root/.aws/credentials; touch /tmp/ok"
    rc = stream_noninteractive(conn, ["podman", "run", "--rm", "--read-only", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", image, "bash", "-lc", script])
    if rc != 0:
        return rc
    for provider in AGENT_REGISTRY.ids():
        seed_provider_home(cfg, provider)
        runtime = common_runtime_args(
            cfg,
            provider,
            network_enabled=False,
            runtime_isolation=provider_state_smoke_isolation(provider),
        )
        writable_targets = provider_state_adapter(provider).writable_smoke_targets()
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
    network_provider = AGENT_REGISTRY.ids()[0]
    network_runtime = common_runtime_args(cfg, network_provider)
    rc = stream_noninteractive(
        conn,
        [*network_runtime, cfg["images"][network_provider], "bash", "-lc", "true"],
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
            [*network_runtime, cfg["images"][network_provider], "bash", "-lc", probe_script],
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
    gitnexus_home = pp["runtime"] / "gitnexus" / task
    gitnexus_home.mkdir(parents=True, exist_ok=True)
    gitnexus_home = canonical_dir(gitnexus_home, pp["runtime"], "GitNexus runtime home")
    registry = gitnexus_home / ".gitnexus" / "registry.json"

    runtime = common_runtime_args(cfg, None, ws, readonly=False, reference=pp["reference"], task_meta=meta, git_common=git_common)
    runtime += [
        "-v", f"{gitnexus_home}:/gitnexus-home:rw",
        "-e", "HOME=/gitnexus-home",
    ]
    argv = [*runtime, image, "gitnexus", "analyze", "--skip-agents-md", "--skip-skills"]
    if not registry.is_file():
        argv.append("--force")
    lock_name = task_lock_name(task, rec)
    with lock_one(pp, lock_name, False):
        return execute_runtime_argv(
            cfg,
            conn,
            None,
            argv,
            interactive=False,
        )

def op_run(cfg: dict, conn: socket.socket, fileobj, req: dict) -> int:
    provider = req.get("provider")
    driver = registered_provider(provider, request_error=True)
    project = valid_name(req.get("project"), "project")
    task = valid_name(req.get("task"), "task")
    legacy_mapping = resolve_run_profile_request(
        readonly=bool(req.get("readonly", False)),
        outer_only=bool(req.get("outer_only", False)),
    )
    readonly = legacy_mapping.readonly
    outer_only = legacy_mapping.outer_only
    prompt = req.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 100_000:
        raise RequestError("prompt must be a string <= 100000 characters")
    rec, pp, ws = load_task(cfg, project, task)
    if not readonly and rec.get("status") != "active":
        raise RequestError("write execution is allowed only while task status is active")
    seed_provider_home(cfg, provider)
    meta = pp["tasks"] / f"{task}.json"
    git_common = pp["agent"] / ".git" if rec["mode"] == "parallel" and rec.get("status") != "merged" else None

    context = TaskContext(
        project=project,
        task=task,
        mode=rec["mode"],
        status=rec["status"],
        metadata_path=meta,
        workspace=ws,
        record=rec,
    )
    if outer_only and "outer-only" not in driver.capabilities().compatibility_modes:
        raise RequestError("outer-only mode is Codex-only")
    policy = driver.compile_policy({"readonly": readonly, "outer_only": outer_only})
    run_spec = driver.create_run_spec(context, policy, prompt)
    plan = create_run_execution_plan(
        cfg,
        provider,
        context,
        run_spec,
        readonly=readonly,
        outer_only=outer_only,
        reference=pp["reference"],
        git_common=git_common,
    )
    try:
        require_capabilities(
            plan.required_capabilities,
            driver.capabilities(),
            agent_id=provider,
        )
    except MissingCapabilitiesError as exc:
        raise RequestError(str(exc)) from exc
    lock_name = run_lock_name(task, rec)
    with lock_one(pp, lock_name, readonly):
        send(conn, {"type": "start", "interactive": plan.interaction_mode == "interactive"})
        rc = execute_runtime_plan(cfg, conn, fileobj, plan)
        try:
            send(conn, {"type": "exit", "code": rc})
        except OSError:
            if rc != 130:
                raise
        return rc


def rpc_operations() -> BrokerOperations:
    """Bind current daemon operation callables for RPC dispatch.

    The bindings are created per request so the frozen characterization tests can
    continue monkeypatching operation globals on the compatibility entrypoint.
    """
    return BrokerOperations(
        result_ops={
            "project-init": op_project_init,
            "project-sync": op_project_sync,
            "project-export": op_project_export,
            "project-status": op_project_status,
            "task-start": op_task_start,
            "task-complete": op_task_complete,
            "task-merge": op_task_merge,
            "task-abort": op_task_abort,
            "task-list": op_task_list,
        },
        build=op_build,
        status=op_status,
        versions=op_versions,
        smoke=op_smoke,
        index=op_index,
        auth=op_auth,
        run=op_run,
    )


def handle(conn: socket.socket, cfg: dict) -> None:
    """Compatibility wrapper around the packaged broker RPC server boundary."""
    handle_request(conn, cfg, rpc_operations(), logger=LOG)


def main() -> None:
    cfg = load_config()
    serve_fd3(cfg, handle)


if __name__ == "__main__":
    main()
