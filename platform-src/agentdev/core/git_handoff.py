"""Provider-neutral Git bundle handoff operations."""
from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path
from typing import Callable

from agentdev.core.validation import InputValidationError


INTEGRATION_BRANCH = "agent/integration"

GitRun = Callable[..., object]
GitText = Callable[..., str]


def ensure_git_repo(repo: Path, label: str) -> None:
    """Require an ordinary Git checkout at *repo*."""
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError(f"{label} is not a Git checkout: {repo}")


def ensure_clean(repo: Path, label: str, *, git_text: GitText) -> None:
    """Require a checkout without tracked or untracked changes."""
    if git_text(repo, "status", "--porcelain"):
        raise ValueError(f"{label} has uncommitted or untracked changes")


def create_bundle(
    repo: Path,
    ref: str,
    directory: Path,
    prefix: str,
    *,
    git: GitRun,
    now: dt.datetime | None = None,
    pid: int | None = None,
) -> tuple[str, Path]:
    """Create an atomic mode-0640 Git bundle and return its name and path."""
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    process_id = os.getpid() if pid is None else pid
    name = f"{prefix}-{timestamp}-{process_id}.bundle"
    tmp = directory / f".{name}.tmp"
    final = directory / name
    git(repo, "bundle", "create", tmp, ref)
    os.replace(tmp, final)
    os.chmod(final, 0o640)
    return name, final


def initialize_agent_repository(
    agent: Path,
    bundle: Path,
    main_branch: str,
    created_from: str,
    *,
    git: GitRun,
    git_text: GitText,
) -> str:
    """Create the persistent agent repository from a verified inbound bundle."""
    if agent.is_symlink() or agent.exists():
        raise InputValidationError("agent repository already exists")

    try:
        agent.mkdir(mode=0o700)
        git(agent, "init", "-q")
        git(agent, "bundle", "verify", bundle)
        git(agent, "fetch", bundle, f"refs/heads/{main_branch}:refs/heads/{INTEGRATION_BRANCH}")
        git(agent, "switch", INTEGRATION_BRANCH)
        git(agent, "config", "user.name", "AI Development Agent")
        git(agent, "config", "user.email", "agent@localhost")
        git(agent, "config", "commit.gpgSign", "false")
        head = git_text(agent, "rev-parse", "HEAD")
        if head != created_from:
            raise InputValidationError("initial bundle HEAD does not match project metadata")
    except Exception:
        shutil.rmtree(agent, ignore_errors=True)
        raise

    bundle.unlink()
    return head


def synchronize_agent_repository(
    agent: Path,
    bundle: Path,
    main_branch: str,
    *,
    git: GitRun,
    git_text: GitText,
) -> tuple[str, str]:
    """Apply a human-main bundle to the integration branch without opening human Git state."""
    ensure_clean(agent, "repo/agent", git_text=git_text)
    git(agent, "bundle", "verify", bundle)
    inbound_ref = f"refs/remotes/human-main/{main_branch}"
    git(agent, "fetch", bundle, f"refs/heads/{main_branch}:{inbound_ref}")
    git(agent, "switch", INTEGRATION_BRANCH)
    integration = git_text(agent, "rev-parse", INTEGRATION_BRANCH)
    human = git_text(agent, "rev-parse", inbound_ref)
    if integration == human:
        action = "already-synchronized"
    elif git(agent, "merge-base", "--is-ancestor", integration, human, check=False).returncode == 0:
        git(agent, "merge", "--ff-only", inbound_ref)
        action = "fast-forwarded"
    elif git(agent, "merge-base", "--is-ancestor", human, integration, check=False).returncode == 0:
        action = "integration-ahead"
    else:
        raise InputValidationError(
            "human main and agent/integration diverged; explicit human resolution is required"
        )
    head = git_text(agent, "rev-parse", "HEAD")
    bundle.unlink()
    return action, head


def export_integration_bundle(
    agent: Path,
    outbound: Path,
    *,
    git: GitRun,
    git_text: GitText,
    now: dt.datetime | None = None,
    pid: int | None = None,
) -> tuple[Path, str]:
    """Export the current integration branch as an atomic mode-0640 bundle."""
    ensure_clean(agent, "repo/agent", git_text=git_text)
    head = git_text(agent, "rev-parse", INTEGRATION_BRANCH)
    _name, final = create_bundle(
        agent,
        INTEGRATION_BRANCH,
        outbound,
        "agent-integration",
        git=git,
        now=now,
        pid=pid,
    )
    return final, head


def repository_status(agent: Path, *, git_text: GitText) -> dict[str, object]:
    """Return the frozen Git fields used by project-status."""
    return {
        "branch": git_text(agent, "branch", "--show-current"),
        "head": git_text(agent, "rev-parse", "HEAD"),
        "clean": not bool(git_text(agent, "status", "--porcelain")),
    }
