"""Provider-neutral task dependency validation."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentdev.core.validation import InputValidationError, valid_name

ReadJson = Callable[[Path, str], dict]
GitRun = Callable[..., object]


def normalize_dependencies(task: str, raw: object) -> list[str]:
    """Validate and normalize the frozen task-start dependency field."""
    if not isinstance(raw, list) or len(raw) > 128:
        raise InputValidationError("dependencies must be a list")
    dependencies = [valid_name(value, "dependency") for value in raw]
    if task in dependencies:
        raise InputValidationError("a task cannot depend on itself")
    return dependencies


def validate_dependencies(
    pp: dict[str, Path],
    dependencies: list[str],
    base_commit: str,
    *,
    read_json: ReadJson,
    git: GitRun,
) -> None:
    """Require every dependency to be integrated into *base_commit*."""
    for dep in dependencies:
        dep = valid_name(dep, "dependency")
        path = pp["tasks"] / f"{dep}.json"
        if not path.exists():
            raise InputValidationError(f"dependency metadata does not exist: {dep}")
        dep_rec = read_json(path, "dependency metadata")
        mode = dep_rec.get("mode")
        status = dep_rec.get("status")
        if mode == "parallel":
            if status != "merged":
                raise InputValidationError(
                    f"parallel dependency {dep} must be merged, got {status!r}"
                )
            required_commit = dep_rec.get("merge_commit")
        elif mode == "integration":
            if status != "completed":
                raise InputValidationError(
                    f"integration dependency {dep} must be completed, got {status!r}"
                )
            required_commit = dep_rec.get("head_commit")
        else:
            raise ValueError(f"dependency {dep} has unsupported mode {mode!r}")
        if not isinstance(required_commit, str) or not required_commit:
            raise ValueError(f"dependency {dep} has no recorded integrated commit")
        if git(
            pp["agent"],
            "merge-base",
            "--is-ancestor",
            required_commit,
            base_commit,
            check=False,
        ).returncode != 0:
            raise InputValidationError(
                f"dependency {dep} commit is not present in the current integration base"
            )
