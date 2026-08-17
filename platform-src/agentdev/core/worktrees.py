"""Provider-neutral parallel Git worktree operations."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentdev.core.git_handoff import INTEGRATION_BRANCH, ensure_clean
from agentdev.core.validation import InputValidationError

BRANCH_PREFIX = "agent/"

GitRun = Callable[..., object]
GitText = Callable[..., str]


def create_parallel_worktree(
    pp: dict[str, Path],
    task: str,
    *,
    git: GitRun,
) -> tuple[str, Path]:
    """Create the frozen per-task branch/worktree pair from integration."""
    branch = f"{BRANCH_PREFIX}{task}"
    worktree = pp["worktrees"] / task
    if worktree.exists():
        raise InputValidationError(f"worktree path already exists: {worktree}")
    git(pp["agent"], "worktree", "add", "-b", branch, worktree, INTEGRATION_BRANCH)
    return branch, worktree


def validate_recorded_head(
    pp: dict[str, Path],
    rec: dict,
    workspace: Path,
    *,
    git_text: GitText,
) -> str:
    """Require branch and worktree HEADs to match the completed recorded head."""
    recorded_head = rec.get("head_commit")
    if not isinstance(recorded_head, str) or not recorded_head:
        raise ValueError("completed parallel task has no recorded head_commit")
    branch_head = git_text(pp["agent"], "rev-parse", rec["branch"])
    workspace_head = git_text(workspace, "rev-parse", "HEAD")
    if branch_head != recorded_head or workspace_head != recorded_head:
        raise InputValidationError(
            "parallel task branch changed after completion; re-complete the task before merging"
        )
    return recorded_head


def merge_parallel_worktree(
    pp: dict[str, Path],
    rec: dict,
    workspace: Path,
    *,
    git: GitRun,
    git_text: GitText,
) -> str:
    """Merge exactly the recorded completed head and remove its worktree/branch."""
    ensure_clean(workspace, "parallel task worktree", git_text=git_text)
    ensure_clean(pp["agent"], "repo/agent", git_text=git_text)
    recorded_head = validate_recorded_head(pp, rec, workspace, git_text=git_text)
    git(pp["agent"], "switch", INTEGRATION_BRANCH)
    result = git(pp["agent"], "merge", "--no-ff", "--no-edit", recorded_head, check=False)
    if result.returncode != 0:
        git(pp["agent"], "merge", "--abort", check=False)
        raise InputValidationError("merge failed or conflicted; integration branch restored")
    merge_commit = git_text(pp["agent"], "rev-parse", "HEAD")
    git(pp["agent"], "worktree", "remove", workspace)
    git(pp["agent"], "branch", "-d", rec["branch"])
    return merge_commit


def abort_parallel_worktree(
    pp: dict[str, Path],
    rec: dict,
    workspace: Path,
    *,
    git: GitRun,
) -> None:
    """Remove an active/completed parallel worktree and its task branch."""
    if workspace.exists():
        git(pp["agent"], "worktree", "remove", "--force", workspace)
    if git(
        pp["agent"],
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{rec['branch']}",
        check=False,
    ).returncode == 0:
        git(pp["agent"], "branch", "-D", rec["branch"])
