#!/usr/bin/python3
"""Regression checks for task dependency gating semantics."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTD = ROOT / "platform-src" / "bin" / "agentd"
PROJECT = "deps"


def load_agentd():
    loader = importlib.machinery.SourceFileLoader("agentd_dependency_test", str(AGENTD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load agentd")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run(repo: Path, *args: str, input_text: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        input=input_text,
        capture_output=True,
    )
    return proc.stdout.strip()


def init_fixture(root: Path) -> tuple[dict, Path]:
    project = root / "projects" / PROJECT
    repo = project / "repo" / "agent"
    for rel in (
        "repo/agent",
        "worktrees",
        "tasks",
        "reference",
        "results",
        "runtime",
        "exchange/inbound",
        "exchange/outbound",
    ):
        (project / rel).mkdir(parents=True, exist_ok=True)

    run(repo, "init", "-b", "agent/integration")
    run(repo, "config", "user.name", "Dependency Regression")
    run(repo, "config", "user.email", "dependency-regression@example.invalid")
    (repo / "README.md").write_text("# Dependency regression\n")
    run(repo, "add", "README.md")
    run(repo, "commit", "-m", "Initial fixture")
    (project / "project.json").write_text(json.dumps({"project": PROJECT}) + "\n")
    return {"root": str(root)}, repo


def task_path(root: Path, task: str) -> Path:
    return root / "projects" / PROJECT / "tasks" / f"{task}.json"


def start(agentd, cfg: dict, task: str, *, parallel: bool = False, dependencies=None):
    return agentd.op_task_start(
        cfg,
        {
            "op": "task-start",
            "project": PROJECT,
            "task": task,
            "parallel": parallel,
            "dependencies": [] if dependencies is None else dependencies,
        },
    )


def complete(agentd, cfg: dict, task: str):
    return agentd.op_task_complete(cfg, {"op": "task-complete", "project": PROJECT, "task": task})


def merge(agentd, cfg: dict, task: str):
    return agentd.op_task_merge(cfg, {"op": "task-merge", "project": PROJECT, "task": task})


def abort(agentd, cfg: dict, task: str):
    return agentd.op_task_abort(cfg, {"op": "task-abort", "project": PROJECT, "task": task})


def commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    run(repo, "add", filename)
    run(repo, "commit", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def expect_rejected(label: str, func, contains: str | None = None) -> None:
    try:
        func()
    except ValueError as exc:
        text = str(exc)
        if contains is not None:
            assert contains in text, (label, text, contains)
        print(f"PASS {label}: rejected: {text}")
        return
    raise AssertionError(f"{label}: expected ValueError")


def fresh(agentd):
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    cfg, repo = init_fixture(root)
    return temp, root, cfg, repo


def case_no_dependencies(agentd) -> None:
    temp, root, cfg, _repo = fresh(agentd)
    with temp:
        rec = start(agentd, cfg, "REQ-NONE")
        assert rec["dependencies"] == []
        assert json.loads(task_path(root, "REQ-NONE").read_text())["dependencies"] == []
    print("PASS DEP-01 no dependencies are recorded as an empty list")


def case_request_validation(agentd) -> None:
    temp, _root, cfg, _repo = fresh(agentd)
    with temp:
        expect_rejected(
            "DEP-02 self dependency",
            lambda: start(agentd, cfg, "REQ-SELF", dependencies=["REQ-SELF"]),
            "cannot depend on itself",
        )
        expect_rejected(
            "DEP-03 dependencies must be a list",
            lambda: start(agentd, cfg, "REQ-TYPE", dependencies="REQ-A"),
            "dependencies must be a list",
        )
        expect_rejected(
            "DEP-04 dependency count is bounded",
            lambda: start(agentd, cfg, "REQ-MANY", dependencies=[f"D{i}" for i in range(129)]),
            "dependencies must be a list",
        )
        expect_rejected(
            "DEP-05 dependency names are validated",
            lambda: start(agentd, cfg, "REQ-NAME", dependencies=["../escape"]),
            "invalid dependency",
        )
    print("PASS request-level dependency validation")


def case_missing_dependency(agentd) -> None:
    temp, root, cfg, _repo = fresh(agentd)
    with temp:
        expect_rejected(
            "DEP-06 missing dependency metadata",
            lambda: start(agentd, cfg, "REQ-CHILD", dependencies=["REQ-MISSING"]),
            "dependency metadata does not exist",
        )
        assert not task_path(root, "REQ-CHILD").exists()
    print("PASS missing dependencies do not create task metadata")


def case_integration_dependency(agentd) -> None:
    temp, root, cfg, repo = fresh(agentd)
    with temp:
        parent = start(agentd, cfg, "REQ-SEQ")
        assert parent["mode"] == "integration"
        expect_rejected(
            "DEP-07 active integration dependency",
            lambda: start(agentd, cfg, "REQ-BLOCKED", dependencies=["REQ-SEQ"]),
            "must be completed",
        )
        parent_head = commit(repo, "seq.txt", "seq\n", "Complete sequential dependency")
        parent = complete(agentd, cfg, "REQ-SEQ")
        assert parent["head_commit"] == parent_head
        child = start(agentd, cfg, "REQ-AFTER-SEQ", dependencies=["REQ-SEQ"])
        assert child["dependencies"] == ["REQ-SEQ"]
        assert child["base_commit"] == parent_head
        assert json.loads(task_path(root, "REQ-AFTER-SEQ").read_text())["dependencies"] == ["REQ-SEQ"]
    print("PASS DEP-08 completed integration dependency is accepted and recorded")


def case_parallel_dependency(agentd) -> None:
    temp, root, cfg, repo = fresh(agentd)
    with temp:
        parent = start(agentd, cfg, "REQ-PAR", parallel=True)
        ws = Path(parent["workspace"])
        expect_rejected(
            "DEP-09 active parallel dependency",
            lambda: start(agentd, cfg, "REQ-AFTER-ACTIVE", parallel=True, dependencies=["REQ-PAR"]),
            "must be merged",
        )
        commit(ws, "parallel.txt", "parallel\n", "Complete parallel dependency")
        parent = complete(agentd, cfg, "REQ-PAR")
        assert parent["status"] == "completed"
        expect_rejected(
            "DEP-10 completed-but-unmerged parallel dependency",
            lambda: start(agentd, cfg, "REQ-AFTER-COMPLETED", parallel=True, dependencies=["REQ-PAR"]),
            "must be merged",
        )
        parent = merge(agentd, cfg, "REQ-PAR")
        assert parent["status"] == "merged"
        assert parent["merge_commit"] == run(repo, "rev-parse", "agent/integration")
        child = start(agentd, cfg, "REQ-AFTER-MERGE", dependencies=["REQ-PAR"])
        assert child["dependencies"] == ["REQ-PAR"]
        assert run(repo, "merge-base", "--is-ancestor", parent["merge_commit"], child["base_commit"]) == ""
        assert json.loads(task_path(root, "REQ-AFTER-MERGE").read_text())["dependencies"] == ["REQ-PAR"]
    print("PASS DEP-11 merged parallel dependency is accepted and recorded")


def case_aborted_dependency(agentd) -> None:
    temp, _root, cfg, _repo = fresh(agentd)
    with temp:
        parent = start(agentd, cfg, "REQ-ABORT", parallel=True)
        assert parent["status"] == "active"
        parent = abort(agentd, cfg, "REQ-ABORT")
        assert parent["status"] == "aborted"
        expect_rejected(
            "DEP-12 aborted parallel dependency",
            lambda: start(agentd, cfg, "REQ-AFTER-ABORT", dependencies=["REQ-ABORT"]),
            "must be merged",
        )
    print("PASS aborted dependency is not considered satisfied")


def case_integrated_commit_required(agentd) -> None:
    temp, root, cfg, repo = fresh(agentd)
    with temp:
        tasks = root / "projects" / PROJECT / "tasks"
        base = run(repo, "rev-parse", "HEAD")

        bad_missing = {
            "project": PROJECT,
            "task": "REQ-BAD-MISSING",
            "mode": "integration",
            "status": "completed",
            "head_commit": None,
        }
        (tasks / "REQ-BAD-MISSING.json").write_text(json.dumps(bad_missing) + "\n")
        expect_rejected(
            "DEP-13 completed dependency without integrated commit",
            lambda: start(agentd, cfg, "REQ-CHILD-MISSING", dependencies=["REQ-BAD-MISSING"]),
            "has no recorded integrated commit",
        )

        tree = run(repo, "rev-parse", f"{base}^{{tree}}")
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Dependency Regression",
                "GIT_AUTHOR_EMAIL": "dependency-regression@example.invalid",
                "GIT_COMMITTER_NAME": "Dependency Regression",
                "GIT_COMMITTER_EMAIL": "dependency-regression@example.invalid",
            }
        )
        stale = subprocess.run(
            ["git", "-C", str(repo), "commit-tree", tree],
            check=True,
            text=True,
            input="Unintegrated dependency\n",
            capture_output=True,
            env=env,
        ).stdout.strip()
        assert stale != base
        bad_stale = {
            "project": PROJECT,
            "task": "REQ-BAD-STALE",
            "mode": "integration",
            "status": "completed",
            "head_commit": stale,
        }
        (tasks / "REQ-BAD-STALE.json").write_text(json.dumps(bad_stale) + "\n")
        expect_rejected(
            "DEP-14 dependency commit absent from integration base",
            lambda: start(agentd, cfg, "REQ-CHILD-STALE", dependencies=["REQ-BAD-STALE"]),
            "not present in the current integration base",
        )

        bad_mode = {
            "project": PROJECT,
            "task": "REQ-BAD-MODE",
            "mode": "mystery",
            "status": "completed",
            "head_commit": base,
        }
        (tasks / "REQ-BAD-MODE.json").write_text(json.dumps(bad_mode) + "\n")
        expect_rejected(
            "DEP-15 unsupported dependency mode",
            lambda: start(agentd, cfg, "REQ-CHILD-MODE", dependencies=["REQ-BAD-MODE"]),
            "unsupported mode",
        )
    print("PASS dependency metadata must identify an integrated commit with a supported mode")


def case_multiple_dependencies(agentd) -> None:
    temp, root, cfg, repo = fresh(agentd)
    with temp:
        seq = start(agentd, cfg, "REQ-SEQ-A")
        commit(repo, "seq-a.txt", "A\n", "Sequential dependency A")
        seq = complete(agentd, cfg, "REQ-SEQ-A")

        par = start(agentd, cfg, "REQ-PAR-B", parallel=True)
        ws = Path(par["workspace"])
        commit(ws, "par-b.txt", "B\n", "Parallel dependency B")
        complete(agentd, cfg, "REQ-PAR-B")
        par = merge(agentd, cfg, "REQ-PAR-B")

        child = start(
            agentd,
            cfg,
            "REQ-MULTI",
            dependencies=["REQ-SEQ-A", "REQ-PAR-B"],
        )
        assert child["dependencies"] == ["REQ-SEQ-A", "REQ-PAR-B"]
        assert run(repo, "merge-base", "--is-ancestor", seq["head_commit"], child["base_commit"]) == ""
        assert run(repo, "merge-base", "--is-ancestor", par["merge_commit"], child["base_commit"]) == ""
        saved = json.loads(task_path(root, "REQ-MULTI").read_text())
        assert saved["dependencies"] == ["REQ-SEQ-A", "REQ-PAR-B"]
    print("PASS DEP-16 multiple satisfied dependencies are accepted in request order")


def main() -> None:
    agentd = load_agentd()
    agentd.LOG.disabled = True
    case_no_dependencies(agentd)
    case_request_validation(agentd)
    case_missing_dependency(agentd)
    case_integration_dependency(agentd)
    case_parallel_dependency(agentd)
    case_aborted_dependency(agentd)
    case_integrated_commit_required(agentd)
    case_multiple_dependencies(agentd)
    print("dependency semantics regression checks passed")


if __name__ == "__main__":
    main()
