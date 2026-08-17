"""Provider-neutral identifier, Git-ref, and filesystem-boundary validation."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class InputValidationError(ValueError):
    """Expected rejection of invalid provider-neutral input."""


def is_valid_name(value: object) -> bool:
    """Return whether *value* satisfies the shared project/task name contract."""
    return isinstance(value, str) and NAME_RE.fullmatch(value) is not None


def valid_name(value: object, what: str) -> str:
    """Validate a shared identifier using the frozen v0.1 naming rules."""
    if not is_valid_name(value):
        raise InputValidationError(f"invalid {what}")
    return value


def valid_git_branch(value: object, what: str = "Git branch") -> str:
    """Validate a Git branch name using Git's own check-ref-format rules."""
    if not isinstance(value, str) or len(value) > 255:
        raise InputValidationError(f"invalid {what}")
    proc = subprocess.run(
        ["git", "check-ref-format", "--branch", value],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise InputValidationError(f"invalid {what}: {value!r}")
    return value


def ensure_under(path: Path, root: Path) -> Path:
    """Resolve *path* and require it to remain under the resolved *root*."""
    resolved = path.resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes allowed root: {path}") from exc
    return resolved


def canonical_dir(path: Path, root: Path, label: str) -> Path:
    """Return a canonical in-root directory while rejecting direct symlinks."""
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = ensure_under(path, root)
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")
    return resolved


def canonical_file(path: Path, root: Path, label: str) -> Path:
    """Return a canonical in-root file while rejecting direct symlinks."""
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = ensure_under(path, root)
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    return resolved
