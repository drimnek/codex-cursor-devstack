"""Provider-neutral project layout and Git handoff services."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from agentdev.core.git_handoff import (
    INTEGRATION_BRANCH,
    ensure_git_repo,
    export_integration_bundle,
    initialize_agent_repository,
    repository_status,
    synchronize_agent_repository,
)
from agentdev.core.models import ProjectContext
from agentdev.core.validation import (
    InputValidationError,
    canonical_dir,
    canonical_file,
    ensure_under,
    valid_git_branch,
    valid_name,
)

ReadJson = Callable[[Path, str], dict]
GitRun = Callable[..., object]
GitText = Callable[..., str]


def controller_project_paths(platform_root: Path, project: str) -> dict[str, Path]:
    """Return the human-controller project layout without requiring it to exist."""
    project = valid_name(project, "project")
    return ProjectContext.from_platform_root(Path(platform_root), project).controller_paths()


def resolve_project_root(platform_root: Path, project: str) -> Path:
    """Resolve an existing project root while rejecting namespace escapes."""
    project = valid_name(project, "project")
    context = ProjectContext.from_platform_root(Path(platform_root), project)
    projects = context.root.parent
    candidate = context.root
    if candidate.is_symlink():
        raise InputValidationError("project root must not be a symlink")
    root = ensure_under(candidate, projects)
    if not root.is_dir():
        raise InputValidationError(f"project does not exist: {project}")
    return root


def resolve_project_paths(platform_root: Path, project: str) -> dict[str, Path]:
    """Return canonical agent-side paths for an existing project."""
    root = resolve_project_root(platform_root, project)
    context = ProjectContext.from_project_root(project, root)

    def subdir(path: Path, label: str) -> Path:
        return canonical_dir(path, root, label)

    return {
        "root": root,
        "agent": subdir(context.agent, "agent repository root"),
        "worktrees": subdir(context.worktrees, "worktrees root"),
        "tasks": subdir(context.tasks, "tasks root"),
        "reference": subdir(context.reference, "reference root"),
        "results": subdir(context.results, "results root"),
        "runtime": subdir(context.runtime, "runtime root"),
        "inbound": subdir(context.inbound, "inbound exchange root"),
        "outbound": subdir(context.outbound, "outbound exchange root"),
        "project_meta": context.project_meta,
    }


def initialize_agent_project(
    platform_root: Path,
    project: str,
    bundle_name: str,
    *,
    read_json: ReadJson,
    git: GitRun,
    git_text: GitText,
) -> dict[str, str]:
    """Initialize the agent-owned repository from a human-produced bundle."""
    project = valid_name(project, "project")
    bundle_name = valid_name(bundle_name, "bundle")
    root = resolve_project_root(platform_root, project)
    context = ProjectContext.from_project_root(project, root)
    repo_root = canonical_dir(context.repo_root, root, "repository namespace")
    inbound = canonical_dir(context.inbound, root, "inbound exchange root")
    meta_path = canonical_file(context.project_meta, root, "project metadata")
    meta = read_json(meta_path, "project metadata")
    main_branch = valid_git_branch(meta.get("main_branch"), "main branch")
    created_from = meta.get("created_from")
    if not isinstance(created_from, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", created_from):
        raise ValueError("project metadata has invalid created_from commit")
    bundle = canonical_file(inbound / bundle_name, inbound, "inbound bundle")
    agent = repo_root / "agent"
    head = initialize_agent_repository(
        agent,
        bundle,
        main_branch,
        created_from,
        git=git,
        git_text=git_text,
    )
    return {
        "project": project,
        "integration_branch": INTEGRATION_BRANCH,
        "integration_head": head,
    }


def synchronize_agent_project(
    platform_root: Path,
    project: str,
    bundle_name: str,
    *,
    read_json: ReadJson,
    git: GitRun,
    git_text: GitText,
) -> dict[str, str]:
    """Synchronize human main into the agent integration repository."""
    project = valid_name(project, "project")
    bundle_name = valid_name(bundle_name, "bundle")
    pp = resolve_project_paths(platform_root, project)
    ensure_git_repo(pp["agent"], "repo/agent")
    meta_path = canonical_file(pp["project_meta"], pp["root"], "project metadata")
    meta = read_json(meta_path, "project metadata")
    main_branch = valid_git_branch(meta.get("main_branch"), "main branch")
    bundle = canonical_file(pp["inbound"] / bundle_name, pp["inbound"], "inbound bundle")
    action, head = synchronize_agent_repository(
        pp["agent"],
        bundle,
        main_branch,
        git=git,
        git_text=git_text,
    )
    return {"project": project, "action": action, "integration_head": head}


def export_agent_project(
    platform_root: Path,
    project: str,
    *,
    git: GitRun,
    git_text: GitText,
) -> dict[str, object]:
    """Export the current agent integration history into the outbound exchange."""
    project = valid_name(project, "project")
    pp = resolve_project_paths(platform_root, project)
    ensure_git_repo(pp["agent"], "repo/agent")
    final, head = export_integration_bundle(
        pp["agent"],
        pp["outbound"],
        git=git,
        git_text=git_text,
    )
    return {"project": project, "bundle": str(final), "integration_head": head}


def project_git_status(agent: Path, *, git_text: GitText) -> dict[str, object]:
    """Expose only the Git fields owned by the project-status contract."""
    ensure_git_repo(agent, "repo/agent")
    return repository_status(agent, git_text=git_text)
