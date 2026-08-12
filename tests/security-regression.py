#!/usr/bin/env python3
"""Pre-pilot regression checks for the host/agent trust boundary."""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_extensionless(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


agentd = load_extensionless("agentd_test", ROOT / "platform-src/bin/agentd")


def run(*argv: str, cwd: Path | None = None) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run("git", "-C", str(repo), *args)


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", f"test {name}")
    return git(repo, "rev-parse", "HEAD")


def test_human_controller_does_not_open_agent_git() -> None:
    source = (ROOT / "platform-src/bin/agentctl").read_text()
    tree = ast.parse(source)
    violations = []
    for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {"git", "git_text", "ensure_clean", "ensure_git_repo"} or not node.args:
                continue
            first = node.args[0]
            if (
                isinstance(first, ast.Subscript)
                and isinstance(first.value, ast.Name)
                and first.value.id == "pp"
                and isinstance(first.slice, ast.Constant)
                and first.slice.value == "agent"
            ):
                violations.append((fn.name, node.lineno, node.func.id))
    assert not violations, f"human-side Git access to repo/agent: {violations}"
    assert '"op": "project-init"' in source, "project-init must hand repository creation to agentd"
    assert "grant_agent_rw" not in source, "agent repository must not rely on ACL-only ownership handoff"
    assert '0o3770' in source and 'f"u:{agent}:-wx"' in source, "repo namespace must use sticky split-ownership handoff permissions"


def test_rpc_and_path_validation(tmp: Path) -> None:
    try:
        agentd.validate_request_shape({"op": "ping", "unexpected": True})
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected RPC fields were accepted")

    allowed = tmp / "allowed"
    outside = tmp / "outside"
    allowed.mkdir(); outside.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)
    try:
        agentd.canonical_dir(link, allowed, "test mount")
    except ValueError:
        pass
    else:
        raise AssertionError("symlink mount source was accepted")

    assert agentd.valid_git_branch("feature/main") == "feature/main"
    try:
        agentd.valid_git_branch("../bad")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid Git branch name was accepted")


def create_project(tmp: Path):
    root = tmp / "root"
    pp_root = root / "projects" / "demo"
    for rel in ["repo", "worktrees", "tasks", "reference", "results", "runtime", "exchange/inbound", "exchange/outbound"]:
        (pp_root / rel).mkdir(parents=True, exist_ok=True)

    human = tmp / "human"
    run("git", "init", "-q", "-b", "main", str(human))
    git(human, "config", "user.name", "Human Test")
    git(human, "config", "user.email", "human@example.invalid")
    commit(human, "base.txt", "base\n")

    agent = pp_root / "repo" / "agent"
    run("git", "clone", "-q", "--no-local", str(human), str(agent))
    git(agent, "remote", "remove", "origin")
    git(agent, "switch", "-q", "-c", "agent/integration")
    git(agent, "config", "user.name", "Agent Test")
    git(agent, "config", "user.email", "agent@example.invalid")

    (pp_root / "project.json").write_text(json.dumps({"main_branch": "main"}))
    cfg = {
        "root": str(root),
        "ops_group": "unused-test-group",
        "limits": {"pids": 32, "memory": "128m", "cpus": 1},
        "images": {"base": "unused", "codex": "unused", "cursor": "unused", "intelligence": "unused"},
        "versions": {"platform": "test", "codex": "test", "gitnexus": "test"},
    }
    return cfg, human, agent, pp_root




def test_project_init_is_agent_side(tmp: Path) -> None:
    root = tmp / "root"
    project = root / "projects" / "demo"
    for rel in ["repo", "exchange/inbound", "exchange/outbound", "worktrees", "tasks", "reference", "results", "runtime"]:
        (project / rel).mkdir(parents=True, exist_ok=True)

    human = tmp / "human-init"
    run("git", "init", "-q", "-b", "main", str(human))
    git(human, "config", "user.name", "Human Test")
    git(human, "config", "user.email", "human@example.invalid")
    head = commit(human, "base.txt", "base\n")
    bundle = project / "exchange/inbound/init.bundle"
    git(human, "bundle", "create", str(bundle), "main")
    (project / "project.json").write_text(json.dumps({"main_branch": "main", "created_from": head}))

    cfg = {"root": str(root), "ops_group": "unused-test-group"}
    result = agentd.op_project_init(cfg, {"project": "demo", "bundle": "init.bundle"})
    agent = project / "repo/agent"
    assert result["integration_head"] == head
    assert git(agent, "branch", "--show-current") == "agent/integration"
    assert git(agent, "rev-parse", "HEAD") == head
    assert not bundle.exists(), "consumed initialization bundle should be removed"


def test_project_subroot_symlink_rejected(tmp: Path) -> None:
    cfg, _human, _agent, pp_root = create_project(tmp)
    real_tasks = pp_root / "tasks-real"
    (pp_root / "tasks").rename(real_tasks)
    (pp_root / "tasks").symlink_to(real_tasks, target_is_directory=True)
    try:
        agentd.project_paths(cfg, "demo")
    except ValueError:
        pass
    else:
        raise AssertionError("symlinked project subroot was accepted")

def test_bundle_sync_and_dependency_integrity(tmp: Path) -> None:
    cfg, human, agent, pp_root = create_project(tmp)
    pp = agentd.project_paths(cfg, "demo")

    new_human = commit(human, "human.txt", "human update\n")
    bundle = pp_root / "exchange/inbound/human.bundle"
    git(human, "bundle", "create", str(bundle), "main")
    result = agentd.op_project_sync(cfg, {"project": "demo", "bundle": "human.bundle"})
    assert result["integration_head"] == new_human
    assert not bundle.exists(), "consumed inbound bundle should be removed"

    base = git(agent, "rev-parse", "agent/integration")
    parallel_dep = pp_root / "tasks/REQ-P.json"
    parallel_dep.write_text(json.dumps({
        "project": "demo", "task": "REQ-P", "mode": "parallel", "status": "completed",
        "head_commit": base, "merge_commit": None,
    }))
    try:
        agentd.validate_dependencies(pp, ["REQ-P"], base)
    except ValueError:
        pass
    else:
        raise AssertionError("completed but unmerged parallel dependency was accepted")

    rec = json.loads(parallel_dep.read_text())
    rec["status"] = "merged"; rec["merge_commit"] = base
    parallel_dep.write_text(json.dumps(rec))
    agentd.validate_dependencies(pp, ["REQ-P"], base)


def test_merge_uses_recorded_head(tmp: Path) -> None:
    cfg, _human, agent, pp_root = create_project(tmp)
    started = agentd.op_task_start(cfg, {"project": "demo", "task": "REQ-1", "parallel": True, "dependencies": []})
    ws = Path(started["workspace"])
    first_head = commit(ws, "first.txt", "first\n")
    completed = agentd.op_task_complete(cfg, {"project": "demo", "task": "REQ-1"})
    assert completed["head_commit"] == first_head

    commit(ws, "after-complete.txt", "mutated after completion\n")
    try:
        agentd.op_task_merge(cfg, {"project": "demo", "task": "REQ-1"})
    except ValueError as exc:
        assert "changed after completion" in str(exc)
    else:
        raise AssertionError("merge accepted a branch that moved after task completion")
    assert git(agent, "branch", "--show-current") == "agent/integration"


def main() -> None:
    test_human_controller_does_not_open_agent_git()
    with tempfile.TemporaryDirectory() as d:
        test_rpc_and_path_validation(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_project_init_is_agent_side(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_project_subroot_symlink_rejected(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_bundle_sync_and_dependency_integrity(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_merge_uses_recorded_head(Path(d))
    print("security regression checks passed")


if __name__ == "__main__":
    main()
