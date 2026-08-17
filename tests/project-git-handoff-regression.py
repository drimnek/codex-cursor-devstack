#!/usr/bin/env python3
"""Freeze project and Git bundle handoff behavior after CORE-003."""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.core.git_handoff import INTEGRATION_BRANCH, create_bundle
from agentdev.core.projects import (
    controller_project_paths,
    export_agent_project,
    initialize_agent_project,
    project_git_status,
    resolve_project_paths,
    synchronize_agent_project,
)


def git(repo: Path, *args: str, capture=False, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *map(str, args)],
        check=check,
        text=True,
        capture_output=True,
    )


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args, capture=True).stdout.strip()


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"test {name}")
    return git_text(repo, "rev-parse", "HEAD")


def read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"cannot read {what}: {path}: {exc}") from exc


def make_project(root: Path, project: str) -> dict[str, Path]:
    pp = controller_project_paths(root, project)
    for path in (
        pp["repo_root"],
        pp["worktrees"],
        pp["tasks"],
        pp["reference"],
        pp["results"],
        pp["runtime"],
        pp["inbound"],
        pp["outbound"],
    ):
        path.mkdir(parents=True, exist_ok=True)
    return pp


def make_human_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    git(path, "config", "user.name", "Human Test")
    git(path, "config", "user.email", "human@example.invalid")
    return commit(path, "base.txt", "base\n")


def test_project_layout_and_handoff() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        platform_root = tmp / "platform-root"
        human = tmp / "human"
        base = make_human_repo(human)
        pp = make_project(platform_root, "demo")
        pp["project_meta"].write_text(json.dumps({
            "project": "demo",
            "main_branch": "main",
            "integration_branch": INTEGRATION_BRANCH,
            "created_from": base,
        }))

        fixed = dt.datetime(2026, 8, 17, 12, 0, tzinfo=dt.timezone.utc)
        name, bundle = create_bundle(
            human,
            "main",
            pp["inbound"],
            "project-init",
            git=git,
            now=fixed,
            pid=1234,
        )
        assert name == "project-init-20260817T120000Z-1234.bundle"
        assert bundle == pp["inbound"] / name
        assert stat.S_IMODE(bundle.stat().st_mode) == 0o640

        initialized = initialize_agent_project(
            platform_root,
            "demo",
            name,
            read_json=read_json,
            git=git,
            git_text=git_text,
        )
        assert initialized == {
            "project": "demo",
            "integration_branch": INTEGRATION_BRANCH,
            "integration_head": base,
        }
        assert not bundle.exists(), "successful initialization must consume inbound bundle"
        assert git_text(pp["agent"], "branch", "--show-current") == INTEGRATION_BRANCH
        assert git_text(pp["agent"], "rev-parse", "HEAD") == base

        resolved = resolve_project_paths(platform_root, "demo")
        assert resolved["agent"] == pp["agent"].resolve()
        assert resolved["inbound"] == pp["inbound"].resolve()

        human_head = commit(human, "human.txt", "human update\n")
        sync_name, sync_bundle = create_bundle(
            human,
            "main",
            pp["inbound"],
            "human-main",
            git=git,
            now=fixed,
            pid=1235,
        )
        synced = synchronize_agent_project(
            platform_root,
            "demo",
            sync_name,
            read_json=read_json,
            git=git,
            git_text=git_text,
        )
        assert synced == {
            "project": "demo",
            "action": "fast-forwarded",
            "integration_head": human_head,
        }
        assert not sync_bundle.exists(), "successful synchronization must consume inbound bundle"

        status = project_git_status(pp["agent"], git_text=git_text)
        assert status == {
            "branch": INTEGRATION_BRANCH,
            "head": human_head,
            "clean": True,
        }

        exported = export_agent_project(
            platform_root,
            "demo",
            git=git,
            git_text=git_text,
        )
        exported_bundle = Path(exported["bundle"])
        assert exported["project"] == "demo"
        assert exported["integration_head"] == human_head
        assert exported_bundle.parent == pp["outbound"].resolve()
        assert exported_bundle.is_file()
        assert stat.S_IMODE(exported_bundle.stat().st_mode) == 0o640
        git(pp["agent"], "bundle", "verify", exported_bundle)


def test_invalid_bundle_is_retained_and_partial_repo_removed() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        platform_root = tmp / "platform-root"
        human = tmp / "human"
        base = make_human_repo(human)
        pp = make_project(platform_root, "broken")
        pp["project_meta"].write_text(json.dumps({
            "project": "broken",
            "main_branch": "main",
            "integration_branch": INTEGRATION_BRANCH,
            "created_from": base,
        }))
        bundle = pp["inbound"] / "broken.bundle"
        bundle.write_text("not a git bundle\n")

        try:
            initialize_agent_project(
                platform_root,
                "broken",
                bundle.name,
                read_json=read_json,
                git=git,
                git_text=git_text,
            )
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("invalid inbound bundle was accepted")

        assert bundle.exists(), "failed initialization must retain inbound bundle"
        assert not pp["agent"].exists(), "failed initialization must remove partial agent repository"


def test_core_boundary_is_provider_neutral() -> None:
    core_sources = [
        PLATFORM / "agentdev/core/projects.py",
        PLATFORM / "agentdev/core/git_handoff.py",
    ]
    forbidden = (
        "agentdev.agents",
        "agentdev.runtime",
        "agentdev.policy",
        "codex",
        "cursor",
    )
    for path in core_sources:
        source = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{path.name} imports or embeds provider/runtime concern {token!r}"

    daemon_source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    assert '"bundle", "verify"' not in daemon_source
    assert '"bundle", "create"' not in daemon_source
    assert "def project_root(" in daemon_source
    assert "def project_paths(" in daemon_source


def main() -> None:
    test_project_layout_and_handoff()
    test_invalid_bundle_is_retained_and_partial_repo_removed()
    test_core_boundary_is_provider_neutral()
    print("project Git handoff regression checks passed")


if __name__ == "__main__":
    main()
