#!/usr/bin/python3
"""Regression checks for human-side project discovery."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTCTL = ROOT / "platform-src" / "bin" / "agentctl"


def load_agentctl():
    loader = importlib.machinery.SourceFileLoader("agentctl_project_list_test", str(AGENTCTL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load agentctl")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def make_ready(projects: Path, name: str) -> Path:
    root = projects / name
    (root / "repo" / "agent").mkdir(parents=True)
    (root / "project.json").write_text('{"project": "%s"}\n' % name)
    return root


def main() -> None:
    agentctl = load_agentctl()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        projects = root / "projects"
        projects.mkdir()

        # Ready projects sort alphabetically regardless of creation order.
        make_ready(projects, "zeta")
        make_ready(projects, "beta")

        # A valid project namespace can be visible while initialization is incomplete.
        alpha = projects / "alpha"
        alpha.mkdir()
        (alpha / "project.json").write_text('{"project": "alpha"}\n')

        # Invalid names and non-directories are not exposed as projects.
        make_ready(projects, "_invalid")
        (projects / "plain-file").write_text("not a project\n")

        # A project-root symlink must not be followed.
        outside = root / "outside"
        make_ready(outside, "target")
        (projects / "linked").symlink_to(outside / "target", target_is_directory=True)

        # Symlinked structural markers make a valid project incomplete rather than ready.
        unsafe_meta = projects / "unsafe-meta"
        (unsafe_meta / "repo" / "agent").mkdir(parents=True)
        (unsafe_meta / "project.json").symlink_to(outside / "target" / "project.json")

        unsafe_agent = projects / "unsafe-agent"
        (unsafe_agent / "repo").mkdir(parents=True)
        (unsafe_agent / "project.json").write_text('{"project": "unsafe-agent"}\n')
        (unsafe_agent / "repo" / "agent").symlink_to(
            outside / "target" / "repo" / "agent",
            target_is_directory=True,
        )

        expected = [
            {"project": "alpha", "state": "incomplete"},
            {"project": "beta", "state": "ready"},
            {"project": "unsafe-agent", "state": "incomplete"},
            {"project": "unsafe-meta", "state": "incomplete"},
            {"project": "zeta", "state": "ready"},
        ]

        cfg = {"root": str(root)}
        actual = agentctl.discover_projects(cfg)
        assert actual == expected, (actual, expected)

        parsed = agentctl.parser().parse_args(["project-list"])
        assert parsed.cmd == "project-list"
        assert parsed.oneline is False

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            agentctl.cmd_project_list(cfg, parsed)
        assert json.loads(stdout.getvalue()) == expected

        parsed_oneline = agentctl.parser().parse_args(["project-list", "--oneline"])
        assert parsed_oneline.cmd == "project-list"
        assert parsed_oneline.oneline is True

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            agentctl.cmd_project_list(cfg, parsed_oneline)
        assert stdout.getvalue() == (
            "alpha:incomplete\n"
            "beta:ready\n"
            "unsafe-agent:incomplete\n"
            "unsafe-meta:incomplete\n"
            "zeta:ready\n"
        )

    print("project list regression checks passed")


if __name__ == "__main__":
    main()
