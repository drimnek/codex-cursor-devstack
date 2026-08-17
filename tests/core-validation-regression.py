#!/usr/bin/env python3
"""Freeze provider-neutral validation and domain-model behavior after CORE-002."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.core.models import ExecutorSpec, ProjectContext, ProviderStateSpec, TaskContext
from agentdev.core.validation import (
    InputValidationError,
    canonical_dir,
    canonical_file,
    ensure_under,
    is_valid_name,
    valid_git_branch,
    valid_name,
)


def load_entrypoint(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def expect(exc_type, fn, *args):
    try:
        fn(*args)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}{args!r}")


def test_identifier_contract() -> None:
    for value in ("a", "A-1", "REQ_1", "x.y", "a" * 64):
        assert is_valid_name(value)
        assert valid_name(value, "name") == value

    for value in (None, "", ".bad", "-bad", "_bad", "bad/name", "bad name", "a" * 65):
        assert not is_valid_name(value)
        expect(InputValidationError, valid_name, value, "name")

    assert valid_git_branch("feature/main") == "feature/main"
    expect(InputValidationError, valid_git_branch, "../bad")
    expect(InputValidationError, valid_git_branch, "x" * 256)


def test_path_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        allowed = tmp / "allowed"
        inside = allowed / "inside"
        outside = tmp / "outside"
        allowed.mkdir(); inside.mkdir(); outside.mkdir()
        payload = inside / "payload.txt"
        payload.write_text("ok\n")

        assert ensure_under(inside, allowed) == inside.resolve()
        assert canonical_dir(inside, allowed, "inside") == inside.resolve()
        assert canonical_file(payload, allowed, "payload") == payload.resolve()

        direct_link = allowed / "direct-link"
        direct_link.symlink_to(inside, target_is_directory=True)
        expect(ValueError, canonical_dir, direct_link, allowed, "direct link")

        escape = allowed / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        expect(ValueError, ensure_under, escape, allowed)

        file_link = allowed / "payload-link"
        file_link.symlink_to(payload)
        expect(ValueError, canonical_file, file_link, allowed, "payload link")


def test_domain_models() -> None:
    root = Path("/srv/example")
    project = ProjectContext.from_platform_root(root, "demo")
    paths = project.controller_paths()
    assert project.root == root / "projects/demo"
    assert paths["repo_root"] == root / "projects/demo/repo"
    assert paths["main"] == root / "projects/demo/repo/main"
    assert paths["agent"] == root / "projects/demo/repo/agent"
    assert paths["inbound"] == root / "projects/demo/exchange/inbound"
    assert paths["project_meta"] == root / "projects/demo/project.json"

    task = TaskContext(
        project="demo",
        task="REQ-1",
        mode="parallel",
        status="active",
        metadata_path=project.tasks / "REQ-1.json",
        workspace=project.worktrees / "REQ-1",
        record={"project": "demo", "task": "REQ-1"},
    )
    assert task.task == "REQ-1"
    assert task.workspace == project.worktrees / "REQ-1"

    state = ProviderStateSpec("volume-name", "/provider/state")
    executor = ExecutorSpec("example-image", ("tool", "--version"), ("src:/dst:ro",))
    assert state.target == "/provider/state" and not state.read_only
    assert executor.argv == ("tool", "--version")

    models_source = (PLATFORM / "agentdev/core/models.py").read_text(encoding="utf-8").lower()
    assert "codex" not in models_source
    assert "cursor" not in models_source


def test_compatibility_exports() -> None:
    agentd = load_entrypoint("agentd_core_validation_regression", PLATFORM / "bin/agentd")
    agentctl = load_entrypoint("agentctl_core_validation_regression", PLATFORM / "bin/agentctl")

    assert agentd.RequestError is InputValidationError
    assert agentd.valid_name is valid_name
    assert agentd.valid_git_branch is valid_git_branch
    assert agentd.canonical_dir is canonical_dir
    assert agentd.canonical_file is canonical_file
    assert agentd.ensure_under is ensure_under

    assert agentctl.check_name("demo", "project") == "demo"
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            agentctl.check_name("bad/name", "project")
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("agentctl accepted invalid project name")
    assert stderr.getvalue().strip() == (
        "agentctl: invalid project name 'bad/name'; "
        "use letters, numbers, dot, underscore or dash"
    )

    daemon_source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    cli_source = (PLATFORM / "agentdev/broker/cli.py").read_text(encoding="utf-8")
    assert "def valid_name(" not in daemon_source
    assert "def canonical_dir(" not in daemon_source
    assert "NAME_RE =" not in daemon_source
    assert "NAME_RE =" not in cli_source
    assert "NAME_RE." not in cli_source


def main() -> None:
    test_identifier_contract()
    test_path_contract()
    test_domain_models()
    test_compatibility_exports()
    print("core validation regression checks passed")


if __name__ == "__main__":
    main()
