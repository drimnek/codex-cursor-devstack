"""Provider-neutral domain data structures used during v0.2 extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Canonical project-layout vocabulary without lifecycle behavior."""

    project: str
    root: Path
    repo_root: Path
    main: Path
    agent: Path
    worktrees: Path
    tasks: Path
    reference: Path
    results: Path
    runtime: Path
    exchange: Path
    inbound: Path
    outbound: Path
    project_meta: Path

    @classmethod
    def from_platform_root(cls, platform_root: Path, project: str) -> "ProjectContext":
        return cls.from_project_root(project, Path(platform_root) / "projects" / project)

    @classmethod
    def from_project_root(cls, project: str, root: Path) -> "ProjectContext":
        root = Path(root)
        repo_root = root / "repo"
        exchange = root / "exchange"
        return cls(
            project=project,
            root=root,
            repo_root=repo_root,
            main=repo_root / "main",
            agent=repo_root / "agent",
            worktrees=root / "worktrees",
            tasks=root / "tasks",
            reference=root / "reference",
            results=root / "results",
            runtime=root / "runtime",
            exchange=exchange,
            inbound=exchange / "inbound",
            outbound=exchange / "outbound",
            project_meta=root / "project.json",
        )

    def controller_paths(self) -> dict[str, Path]:
        """Return the legacy human-controller project path mapping unchanged."""
        return {
            "root": self.root,
            "repo_root": self.repo_root,
            "main": self.main,
            "agent": self.agent,
            "worktrees": self.worktrees,
            "tasks": self.tasks,
            "reference": self.reference,
            "results": self.results,
            "runtime": self.runtime,
            "exchange": self.exchange,
            "inbound": self.inbound,
            "outbound": self.outbound,
            "project_meta": self.project_meta,
        }


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Validated task identity plus its current metadata/workspace view."""

    project: str
    task: str
    mode: str
    status: str
    metadata_path: Path
    workspace: Path
    record: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderStateSpec:
    """Provider-state mount description shared by driver and runtime layers."""

    source: str
    target: str
    read_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source or "\x00" in self.source:
            raise ValueError("provider state source must be a non-empty string without NUL")
        if not isinstance(self.target, str) or not self.target or "\x00" in self.target:
            raise ValueError("provider state target must be a non-empty string without NUL")
        target = PurePosixPath(self.target)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("provider state target must be an absolute container path without '..'")
        if type(self.read_only) is not bool:
            raise ValueError("provider state read_only must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "read_only": self.read_only}


@dataclass(frozen=True, slots=True)
class ExecutorSpec:
    """Neutral placeholder for a future resolved executor invocation."""

    image: str
    argv: tuple[str, ...] = ()
    mounts: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
