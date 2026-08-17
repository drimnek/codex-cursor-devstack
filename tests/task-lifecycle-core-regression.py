#!/usr/bin/env python3
"""Freeze task/dependency/worktree behavior while extracting it from agentd."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"


def load_entrypoint(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip()


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def create_project(root: Path) -> tuple[dict, dict[str, Path]]:
    project = root / "projects" / "demo"
    paths = {
        "root": project,
        "agent": project / "repo" / "agent",
        "worktrees": project / "worktrees",
        "tasks": project / "tasks",
        "reference": project / "reference",
        "results": project / "results",
        "runtime": project / "runtime",
        "inbound": project / "exchange" / "inbound",
        "outbound": project / "exchange" / "outbound",
    }
    for key, path in paths.items():
        if key != "agent":
            path.mkdir(parents=True, exist_ok=True)
    paths["agent"].mkdir(parents=True)
    git(paths["agent"], "init", "-q", "-b", "agent/integration")
    git(paths["agent"], "config", "user.name", "Task Regression")
    git(paths["agent"], "config", "user.email", "task@example.invalid")
    commit(paths["agent"], "base.txt", "base\n")
    return {"root": str(root)}, paths


def check_core_boundary() -> None:
    sys.path.insert(0, str(PLATFORM))
    try:
        from agentdev.core.dependencies import normalize_dependencies
        from agentdev.core import dependencies, tasks, worktrees

        assert normalize_dependencies("REQ-C", ["REQ-A", "REQ-B"]) == ["REQ-A", "REQ-B"]
        try:
            normalize_dependencies("REQ-A", ["REQ-A"])
        except ValueError as exc:
            assert "cannot depend on itself" in str(exc)
        else:
            raise AssertionError("self dependency was accepted")

        for module in (dependencies, tasks, worktrees):
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            for forbidden in ("codex", "cursor", "podman", "provider_state"):
                assert forbidden not in source, (module.__name__, forbidden)
    finally:
        sys.path.pop(0)


def check_lifecycle() -> None:
    agentd = load_entrypoint("agentd_task_core_regression", PLATFORM / "bin" / "agentd")
    agentd.LOG.disabled = True

    def quiet_git(repo: Path, *args: str, capture=False, check=True):
        return subprocess.run(
            ["git", "-C", str(repo), *map(str, args)],
            check=check,
            text=True,
            capture_output=True,
        )

    agentd.git = quiet_git

    with tempfile.TemporaryDirectory() as td:
        cfg, pp = create_project(Path(td))

        sequential = agentd.op_task_start(
            cfg,
            {"project": "demo", "task": "REQ-SEQ", "parallel": False, "dependencies": []},
        )
        assert sequential["mode"] == "integration"
        assert sequential["branch"] == "agent/integration"
        seq_head = commit(pp["agent"], "sequential.txt", "sequential\n")
        sequential = agentd.op_task_complete(cfg, {"project": "demo", "task": "REQ-SEQ"})
        assert sequential["status"] == "completed"
        assert sequential["head_commit"] == seq_head

        parallel = agentd.op_task_start(
            cfg,
            {
                "project": "demo",
                "task": "REQ-PAR",
                "parallel": True,
                "dependencies": ["REQ-SEQ"],
            },
        )
        assert parallel["mode"] == "parallel"
        assert parallel["branch"] == "agent/REQ-PAR"
        workspace = Path(parallel["workspace"])
        par_head = commit(workspace, "parallel.txt", "parallel\n")
        parallel = agentd.op_task_complete(cfg, {"project": "demo", "task": "REQ-PAR"})
        assert parallel["head_commit"] == par_head
        merged = agentd.op_task_merge(cfg, {"project": "demo", "task": "REQ-PAR"})
        assert merged["status"] == "merged"
        assert merged["workspace"] == str(pp["agent"])
        assert not workspace.exists()
        assert git(pp["agent"], "branch", "--show-current") == "agent/integration"
        assert git(pp["agent"], "merge-base", "--is-ancestor", par_head, merged["merge_commit"]) == ""

        abortable = agentd.op_task_start(
            cfg,
            {
                "project": "demo",
                "task": "REQ-ABORT",
                "parallel": True,
                "dependencies": ["REQ-PAR"],
            },
        )
        abort_workspace = Path(abortable["workspace"])
        aborted = agentd.op_task_abort(cfg, {"project": "demo", "task": "REQ-ABORT"})
        assert aborted["status"] == "aborted"
        assert not abort_workspace.exists()

        records = {record["task"]: record for record in agentd.op_task_list(cfg, {"project": "demo"})}
        assert records["REQ-SEQ"]["status"] == "completed"
        assert records["REQ-PAR"]["status"] == "merged"
        assert records["REQ-ABORT"]["status"] == "aborted"

        # Frozen compatibility surface used by existing characterization tests.
        assert callable(agentd.task_records)
        assert callable(agentd.load_task)
        assert callable(agentd.validate_dependencies)
        assert agentd.BRANCH_PREFIX == "agent/"


def check_broker_is_orchestration_only() -> None:
    source = (PLATFORM / "agentdev" / "broker" / "daemon.py").read_text(encoding="utf-8")
    for moved_literal in (
        '"worktree", "add"',
        '"worktree", "remove"',
        '"merge", "--no-ff"',
        "parallel dependency {dep}",
        "completed parallel task has no recorded head_commit",
    ):
        assert moved_literal not in source, moved_literal
    for handler in ("op_task_start", "op_task_complete", "op_task_merge", "op_task_abort", "op_task_list"):
        assert f"def {handler}" in source


def main() -> None:
    check_core_boundary()
    check_lifecycle()
    check_broker_is_orchestration_only()
    print("task lifecycle core regression checks passed")


if __name__ == "__main__":
    main()
