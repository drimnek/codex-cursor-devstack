#!/usr/bin/env python3
"""Freeze the initial v0.2 package layout and thin-entrypoint boundary."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
PACKAGE = PLATFORM / "agentdev"


def load_entrypoint(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main() -> None:
    expected_packages = (
        "broker",
        "core",
        "execution",
        "policy",
        "runtime",
        "agents",
    )
    assert (PACKAGE / "__init__.py").is_file()
    for name in expected_packages:
        assert (PACKAGE / name / "__init__.py").is_file(), name

    daemon_impl = PACKAGE / "broker" / "daemon.py"
    cli_impl = PACKAGE / "broker" / "cli.py"
    rpc_impl = PACKAGE / "broker" / "rpc.py"
    core_models = PACKAGE / "core" / "models.py"
    core_validation = PACKAGE / "core" / "validation.py"
    core_projects = PACKAGE / "core" / "projects.py"
    core_git_handoff = PACKAGE / "core" / "git_handoff.py"
    core_tasks = PACKAGE / "core" / "tasks.py"
    core_dependencies = PACKAGE / "core" / "dependencies.py"
    core_worktrees = PACKAGE / "core" / "worktrees.py"
    core_locking = PACKAGE / "core" / "locking.py"
    agent_driver_base = PACKAGE / "agents" / "base.py"
    assert daemon_impl.is_file()
    assert cli_impl.is_file()
    assert rpc_impl.is_file()
    assert core_models.is_file()
    assert core_validation.is_file()
    assert core_projects.is_file()
    assert core_git_handoff.is_file()
    assert core_tasks.is_file()
    assert core_dependencies.is_file()
    assert core_worktrees.is_file()
    assert core_locking.is_file()
    assert agent_driver_base.is_file()

    for entrypoint in (PLATFORM / "bin" / "agentd", PLATFORM / "bin" / "agentctl"):
        source = entrypoint.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 12, entrypoint
        assert "agentdev" in source and "broker" in source
        assert "exec(compile(" in source

    agentd = load_entrypoint("agentd_layout_regression", PLATFORM / "bin" / "agentd")
    agentctl = load_entrypoint("agentctl_layout_regression", PLATFORM / "bin" / "agentctl")

    assert Path(agentd.main.__code__.co_filename).resolve() == daemon_impl.resolve()
    assert Path(agentctl.main.__code__.co_filename).resolve() == cli_impl.resolve()
    assert hasattr(agentd, "handle")
    assert hasattr(agentd, "common_runtime_args")
    assert hasattr(agentctl, "parser")
    assert hasattr(agentctl, "runtime_request")

    # Preserve the existing characterization-test behavior during mechanical
    # extraction: monkeypatches on the entrypoint module must still affect the
    # implementation function globals until those tests migrate to package imports.
    original = agentd.ensure_volume
    sentinel = lambda _name: None
    agentd.ensure_volume = sentinel
    try:
        assert agentd.prepare_provider_state.__globals__["ensure_volume"] is sentinel
    finally:
        agentd.ensure_volume = original

    sys.path.insert(0, str(PLATFORM))
    try:
        from agentdev.agents.base import AgentCapabilities, AgentDriver, RunSpec
        from agentdev.broker import cli, daemon, rpc

        assert inspect.isabstract(AgentDriver)
        assert AgentCapabilities(frozenset({"readonly"}))
        assert RunSpec(("agent",)).argv == ("agent",)
        assert hasattr(daemon, "main") and hasattr(daemon, "handle")
        assert hasattr(cli, "main") and hasattr(cli, "parser")
        assert hasattr(rpc, "handle_request") and hasattr(rpc, "BrokerOperations")
    finally:
        sys.path.pop(0)

    print("modular package layout regression checks passed")


if __name__ == "__main__":
    main()
