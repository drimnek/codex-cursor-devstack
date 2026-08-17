"""Provider-neutral task metadata and lifecycle services."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from agentdev.core.dependencies import normalize_dependencies
from agentdev.core.git_handoff import INTEGRATION_BRANCH, ensure_clean, ensure_git_repo
from agentdev.core.models import TaskContext
from agentdev.core.projects import resolve_project_paths
from agentdev.core.validation import InputValidationError, canonical_dir, canonical_file, valid_name
from agentdev.core.worktrees import (
    abort_parallel_worktree,
    create_parallel_worktree,
    merge_parallel_worktree,
)

ReadJson = Callable[[Path, str], dict]
WriteJson = Callable[[Path, dict], None]
GitRun = Callable[..., object]
GitText = Callable[..., str]
NowIso = Callable[[], str]
DependencyValidator = Callable[[dict[str, Path], list[str], str], None]
RecordsReader = Callable[[dict[str, Path]], list[dict]]
RecordFilter = Callable[[list[dict]], list[dict]]


def task_records(pp: dict[str, Path]) -> list[dict]:
    """Return task metadata records in the existing deterministic filename order."""
    records = []
    for path in sorted(pp["tasks"].glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except Exception:
            continue
    return records


def active_sequential(records: list[dict]) -> list[dict]:
    return [
        record
        for record in records
        if record.get("mode") == "integration" and record.get("status") == "active"
    ]


def pending_parallel(records: list[dict]) -> list[dict]:
    return [
        record
        for record in records
        if record.get("mode") == "parallel" and record.get("status") in {"active", "completed"}
    ]


def task_meta_path(pp: dict[str, Path], task: str) -> Path:
    task = valid_name(task, "task")
    return pp["tasks"] / f"{task}.json"


def load_task(
    platform_root: Path,
    project: str,
    task: str,
    *,
    read_json: ReadJson,
) -> tuple[dict, dict[str, Path], Path]:
    """Load and validate task identity, mode, state, and workspace location."""
    task = valid_name(task, "task")
    pp = resolve_project_paths(Path(platform_root), project)
    meta_path = canonical_file(task_meta_path(pp, task), pp["tasks"], "task metadata")
    rec = read_json(meta_path, "task metadata")
    if rec.get("project") != project or rec.get("task") != task:
        raise ValueError("task metadata identity mismatch")
    mode = rec.get("mode")
    status = rec.get("status")
    if mode not in {"integration", "parallel"}:
        raise ValueError("unsupported task mode")
    if status == "aborted":
        raise InputValidationError("task is aborted")
    workspace = pp["agent"] if mode == "integration" or status == "merged" else pp["worktrees"] / task
    workspace_root = pp["agent"] if mode == "integration" or status == "merged" else pp["worktrees"]
    workspace = canonical_dir(
        workspace,
        workspace_root if mode == "parallel" and status != "merged" else pp["root"],
        "task workspace",
    )
    context = TaskContext(
        project=project,
        task=task,
        mode=mode,
        status=status,
        metadata_path=meta_path,
        workspace=workspace,
        record=rec,
    )
    return context.record, pp, context.workspace


def prepare_task_start_request(
    task: str,
    parallel: object,
    dependencies_raw: object,
) -> tuple[str, bool, list[str]]:
    """Validate task-start fields before project resolution, preserving RPC error order."""
    task = valid_name(task, "task")
    dependencies = normalize_dependencies(task, dependencies_raw)
    return task, bool(parallel), dependencies


def prepare_task_start_target(pp: dict[str, Path], task: str) -> Path:
    """Validate the resolved project target before the integration lock is acquired."""
    ensure_git_repo(pp["agent"], "repo/agent")
    path = task_meta_path(pp, task)
    if path.exists():
        raise InputValidationError("task metadata already exists")
    return path


def start_task_locked(
    pp: dict[str, Path],
    project: str,
    task: str,
    parallel: bool,
    dependencies: list[str],
    metadata_path: Path,
    *,
    records_reader: RecordsReader,
    active_sequential_filter: RecordFilter,
    pending_parallel_filter: RecordFilter,
    dependency_validator: DependencyValidator,
    write_json: WriteJson,
    git: GitRun,
    git_text: GitText,
    now_iso: NowIso,
) -> dict:
    """Start a task while the caller holds the integration lock."""
    ensure_clean(pp["agent"], "repo/agent", git_text=git_text)
    git(pp["agent"], "switch", INTEGRATION_BRANCH)
    base = git_text(pp["agent"], "rev-parse", "HEAD")
    dependency_validator(pp, dependencies, base)
    records = records_reader(pp)
    if parallel:
        if active_sequential_filter(records):
            raise InputValidationError(
                "cannot start a parallel task while an integration task is active"
            )
        branch, worktree = create_parallel_worktree(pp, task, git=git)
        mode = "parallel"
        workspace = str(worktree)
    else:
        if active_sequential_filter(records):
            raise InputValidationError("another integration task is already active")
        if pending_parallel_filter(records):
            raise InputValidationError(
                "parallel tasks must be merged/aborted before sequential work continues"
            )
        branch = INTEGRATION_BRANCH
        mode = "integration"
        workspace = str(pp["agent"])

    rec = {
        "project": project,
        "task": task,
        "mode": mode,
        "branch": branch,
        "base_commit": base,
        "head_commit": None,
        "status": "active",
        "dependencies": dependencies,
        "started_at": now_iso(),
        "completed_at": None,
        "merged_at": None,
        "workspace": workspace,
    }
    write_json(metadata_path, rec)
    return rec


def validate_task_completion(rec: dict) -> None:
    if rec.get("status") != "active":
        raise InputValidationError("task status must be active")


def complete_task_locked(
    pp: dict[str, Path],
    task: str,
    rec: dict,
    workspace: Path,
    *,
    write_json: WriteJson,
    git_text: GitText,
    now_iso: NowIso,
) -> dict:
    """Record the completed task head while the caller holds its selected lock."""
    ensure_clean(workspace, "task workspace", git_text=git_text)
    branch = git_text(workspace, "branch", "--show-current")
    if branch != rec["branch"]:
        raise InputValidationError(f"task workspace branch mismatch: {branch!r}")
    rec["head_commit"] = git_text(workspace, "rev-parse", "HEAD")
    rec["status"] = "completed"
    rec["completed_at"] = now_iso()
    write_json(task_meta_path(pp, task), rec)
    return rec


def validate_task_merge(rec: dict) -> None:
    if rec.get("mode") != "parallel" or rec.get("status") != "completed":
        raise InputValidationError("task-merge requires a completed parallel task")


def merge_task_locked(
    pp: dict[str, Path],
    task: str,
    rec: dict,
    workspace: Path,
    *,
    write_json: WriteJson,
    git: GitRun,
    git_text: GitText,
    now_iso: NowIso,
) -> dict:
    """Merge a completed parallel task while integration/task locks are held."""
    merge_commit = merge_parallel_worktree(pp, rec, workspace, git=git, git_text=git_text)
    rec["status"] = "merged"
    rec["merged_at"] = now_iso()
    rec["merge_commit"] = merge_commit
    rec["workspace"] = str(pp["agent"])
    write_json(task_meta_path(pp, task), rec)
    return rec


def validate_task_abort(rec: dict) -> None:
    if rec.get("mode") != "parallel" or rec.get("status") not in {"active", "completed"}:
        raise InputValidationError("only active/completed parallel tasks can be aborted")


def abort_task_locked(
    pp: dict[str, Path],
    task: str,
    rec: dict,
    workspace: Path,
    *,
    write_json: WriteJson,
    git: GitRun,
    now_iso: NowIso,
) -> dict:
    """Abort a parallel task while integration/task locks are held."""
    abort_parallel_worktree(pp, rec, workspace, git=git)
    rec["status"] = "aborted"
    rec["aborted_at"] = now_iso()
    write_json(task_meta_path(pp, task), rec)
    return rec
