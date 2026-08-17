#!/usr/bin/env python3
"""Freeze provider-state semantics behind trusted driver/state-adapter metadata."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.base import (
    AgentCapabilities,
    AgentDriver,
    AuthSpec,
    InstallationSpec,
    ProviderPolicyArtifacts,
    RunSpec,
    VersionProbeSpec,
)
from agentdev.agents.registry import AgentRegistry, BUILTIN_AGENT_REGISTRY
from agentdev.agents.state import ProviderStateAdapter
from agentdev.core.models import ProviderStateSpec, TaskContext


def load_agentd():
    path = PLATFORM / "bin" / "agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_state_driver_regression", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ThirdDriver(AgentDriver):
    """Fake third provider proving generic runtime does not know its state path."""

    def id(self) -> str:
        return "third"

    def display_name(self) -> str:
        return "Third"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(workspace_modes=frozenset({"readonly"}))

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return (ProviderStateSpec("third-state-volume", "/opt/third/state"),)

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec("third", "Containerfile.third")

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec(("third", "--version"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(("third", "login"))

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(("third", "status"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        return ProviderPolicyArtifacts()

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        return RunSpec(("third", "run"), interactive=False, policy_artifacts=policy)


class Result:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.stdout = ""


def mount_specs(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "-v"]


def find_run(calls: list[list[str]], mount: str) -> list[str]:
    for call in calls:
        if mount in call:
            return call
    raise AssertionError(f"missing Podman call containing {mount!r}: {calls!r}")


def test_builtin_state_metadata() -> None:
    codex = BUILTIN_AGENT_REGISTRY.get("codex")
    cursor = BUILTIN_AGENT_REGISTRY.get("cursor")

    assert tuple(item.as_dict() for item in codex.state_spec()) == (
        {"source": "agent-dev-codex-state", "target": "/root/.codex", "read_only": False},
    )
    assert tuple(item.as_dict() for item in cursor.state_spec()) == (
        {"source": "agent-dev-cursor-state", "target": "/root/.cursor", "read_only": False},
        {"source": "agent-dev-cursor-auth", "target": "/root/.config/cursor", "read_only": False},
    )

    codex_state = codex.state_adapter()
    assert codex_state.legacy_volume == "agent-dev-codex-home"
    assert codex_state.primary().legacy_path == ".codex"
    assert codex_state.primary().cleanup_after_copy == ("config.toml",)
    assert tuple((item.seed_relative_path, item.target, item.read_only) for item in codex_state.policy_mounts) == (
        ("config.toml", "/root/.codex/config.toml", True),
    )

    cursor_state = cursor.state_adapter()
    assert cursor_state.legacy_volume == "agent-dev-cursor-home"
    assert cursor_state.primary().legacy_path == ".cursor"
    assert cursor_state.volume("auth").legacy_path == ".config/cursor"
    assert cursor_state.volume("auth").marker == ".agent-dev-auth-layout-v1"
    assert cursor_state.reconciliation is not None
    assert cursor_state.reconciliation.volume_key == "state"
    assert cursor_state.reconciliation.seed_relative_path == "cli-config.json"
    assert cursor_state.reconciliation.state_relative_path == "cli-config.json"
    assert cursor_state.reconciliation.managed_field == "permissions"


def test_generic_runtime_consumes_driver_state_spec() -> None:
    agentd = load_agentd()
    original_registry = agentd.AGENT_REGISTRY
    agentd.AGENT_REGISTRY = AgentRegistry((ThirdDriver(),)).freeze()
    try:
        cfg = {
            "root": "/srv/agent-dev",
            "limits": {"pids": 64, "memory": "1g", "cpus": "1"},
        }
        args = agentd.common_runtime_args(cfg, "third")
        mounts = mount_specs(args)
        assert "third-state-volume:/opt/third/state:rw" in mounts
        assert not any("codex" in item or "cursor" in item for item in mounts)
    finally:
        agentd.AGENT_REGISTRY = original_registry


def test_legacy_migration_and_reconciliation_stay_compatible() -> None:
    agentd = load_agentd()
    calls: list[list[str]] = []
    ensured: list[str] = []
    original_run = agentd.subprocess.run
    original_ensure = agentd.ensure_volume

    def fake_run(argv, *args, **kwargs):
        call = list(map(str, argv))
        calls.append(call)
        if call[:3] == ["podman", "volume", "exists"]:
            return Result(0)
        return Result(0)

    agentd.subprocess.run = fake_run
    agentd.ensure_volume = ensured.append
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "platform/seed/codex").mkdir(parents=True)
            (root / "platform/seed/cursor").mkdir(parents=True)
            (root / "platform/seed/codex/config.toml").write_text("approval_policy = 'never'\n")
            (root / "platform/seed/cursor/cli-config.json").write_text('{"permissions": {}}\n')
            cfg = {
                "root": str(root),
                "images": {"base": "base-image"},
            }

            agentd.seed_provider_home(cfg, "codex")
            assert ensured == ["agent-dev-codex-state"]
            migration = find_run(calls, "agent-dev-codex-state:/state:rw")
            assert "agent-dev-codex-home:/legacy:ro" in migration
            assert "/legacy/.codex/." in migration[-1]
            assert ".agent-dev-state-layout-v2" in migration[-1]
            assert "/state/config.toml" in migration[-1]
            assert agentd.provider_policy_mounts(cfg, "codex")[-1].endswith(
                ":/root/.codex/config.toml:ro"
            )

            calls.clear()
            ensured.clear()
            agentd.seed_provider_home(cfg, "cursor")
            assert ensured == ["agent-dev-cursor-state", "agent-dev-cursor-auth"]
            migration = find_run(calls, "agent-dev-cursor-state:/state:rw")
            assert "agent-dev-cursor-auth:/auth:rw" in migration
            assert "agent-dev-cursor-home:/legacy:ro" in migration
            assert "/legacy/.cursor/." in migration[-1]
            assert "/legacy/.config/cursor/." in migration[-1]
            assert ".agent-dev-state-layout-v2" in migration[-1]
            assert ".agent-dev-auth-layout-v1" in migration[-1]

            reconcile = find_run(calls, "agent-dev-cursor-state:/state:rw")
            # There are two matching calls; select the one with the seed mount.
            reconcile = next(
                call for call in calls
                if "agent-dev-cursor-state:/state:rw" in call
                and any("cli-config.json:/seed/cli-config.json:ro" in item for item in call)
            )
            assert ".permissions = $seed[0].permissions" in reconcile[-1]
    finally:
        agentd.subprocess.run = original_run
        agentd.ensure_volume = original_ensure


def test_broker_has_no_provider_state_path_literals() -> None:
    daemon_source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    runtime_block = daemon_source[
        daemon_source.index("def common_runtime_args("):
        daemon_source.index("def stream_noninteractive", daemon_source.index("def common_runtime_args("))
    ]
    for literal in (
        "/root/.codex",
        "/root/.cursor",
        "/root/.config/cursor",
        "agent-dev-codex-state",
        "agent-dev-cursor-state",
        "agent-dev-cursor-auth",
        "agent-dev-codex-home",
        "agent-dev-cursor-home",
    ):
        assert literal not in daemon_source, literal
    assert "driver.state_spec()" in runtime_block
    assert 'provider == "cursor"' not in runtime_block
    assert 'provider == "codex"' not in runtime_block

    state_source = (PLATFORM / "agentdev/agents/state.py").read_text(encoding="utf-8").lower()
    assert "podman" in state_source.splitlines()[2].lower() or "podman" in state_source
    # State metadata is declarative: no runtime invocation implementation.
    assert "subprocess" not in state_source
    assert "socket." not in state_source


def main() -> None:
    test_builtin_state_metadata()
    test_generic_runtime_consumes_driver_state_spec()
    test_legacy_migration_and_reconciliation_stay_compatible()
    test_broker_has_no_provider_state_path_literals()
    print("provider state driver regression checks passed")


if __name__ == "__main__":
    main()
