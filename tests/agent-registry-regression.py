#!/usr/bin/env python3
"""Freeze trusted AgentRegistry behavior before concrete provider extraction."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import sys
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
from agentdev.agents.registry import (
    AgentRegistry,
    AgentRegistryError,
    BUILTIN_AGENT_REGISTRY,
    UnknownAgentError,
)
from agentdev.core.models import ProviderStateSpec, TaskContext


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}")


class FakeDriver(AgentDriver):
    def __init__(self, driver_id: object, name: str = "Fake") -> None:
        self._driver_id = driver_id
        self._name = name

    def id(self) -> str:
        return self._driver_id  # type: ignore[return-value]

    def display_name(self) -> str:
        return self._name

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(workspace_modes=frozenset({"readonly"}))

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return ()

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec("fake", "Containerfile.fake")

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec(("fake", "--version"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(("fake", "login"))

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(("fake", "status"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        return ProviderPolicyArtifacts()

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        return RunSpec(("fake", "run"), interactive=False, policy_artifacts=policy)


def load_agentd():
    path = PLATFORM / "bin" / "agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_registry_regression", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_registration_lookup_and_ordering() -> None:
    registry = AgentRegistry()
    zeta = FakeDriver("zeta")
    alpha = FakeDriver("alpha")
    registry.register(zeta)
    registry.register(alpha)

    assert registry.ids() == ("alpha", "zeta")
    assert registry.drivers() == (alpha, zeta)
    assert registry.get("alpha") is alpha
    assert "alpha" in registry
    assert "missing" not in registry

    expect(AgentRegistryError, registry.register, FakeDriver("alpha", "Duplicate"))
    expect(AgentRegistryError, registry.register, FakeDriver("bad/id"))
    expect(AgentRegistryError, registry.register, FakeDriver(""))
    expect(AgentRegistryError, registry.register, FakeDriver(None))
    expect(AgentRegistryError, registry.register, object())
    expect(UnknownAgentError, registry.get, "missing")
    expect(UnknownAgentError, registry.get, "bad/id")
    expect(UnknownAgentError, registry.get, None)

    assert not registry.frozen
    assert registry.freeze() is registry
    assert registry.frozen
    expect(AgentRegistryError, registry.register, FakeDriver("later"))


def test_builtin_registry_is_fixed_and_fail_closed() -> None:
    registry = BUILTIN_AGENT_REGISTRY
    assert registry.frozen
    assert registry.ids() == ("codex", "cursor")
    assert tuple(driver.id() for driver in registry.drivers()) == ("codex", "cursor")
    assert all(isinstance(driver, AgentDriver) for driver in registry.drivers())

    # DRV-004 replaces the Codex transition bridge with a concrete driver.
    codex = registry.get("codex")
    assert codex.capabilities().compatibility_modes == frozenset({"outer-only"})
    assert codex.state_spec()[0].target == "/root/.codex"
    assert codex.auth_spec().argv == ("codex", "login", "--device-auth")
    assert tuple(item.target for item in registry.get("cursor").state_spec()) == (
        "/root/.cursor",
        "/root/.config/cursor",
    )
    expect(NotImplementedError, registry.get("cursor").auth_spec)
    expect(AgentRegistryError, registry.register, FakeDriver("third"))


def test_registry_is_not_dynamic_plugin_loading() -> None:
    source = (PLATFORM / "agentdev/agents/registry.py").read_text(encoding="utf-8")
    forbidden = (
        "importlib",
        "__import__(",
        "entry_points(",
        "pkg_resources",
        "site-packages",
        "PYTHONPATH",
    )
    for token in forbidden:
        assert token not in source, token
    assert "CodexDriver()" in source
    assert '_LegacyBrokerDriver("cursor"' in source


def test_broker_uses_registry_for_provider_acceptance() -> None:
    agentd = load_agentd()
    source = (PLATFORM / "agentdev/broker/daemon.py").read_text(encoding="utf-8")

    assert "ALLOWED_PROVIDERS" not in source
    assert agentd.AGENT_REGISTRY.ids() == ("codex", "cursor")
    assert agentd.registered_provider("codex").id() == "codex"
    assert agentd.registered_provider("cursor").id() == "cursor"
    expect(ValueError, agentd.registered_provider, "unknown")

    # Public broker operations must still reject a syntactically valid but
    # unregistered provider as a request error before any runtime side effect.
    expect(
        agentd.RequestError,
        agentd.op_auth,
        {"images": {}},
        object(),
        io.BytesIO(),
        "unknown",
    )
    expect(
        agentd.RequestError,
        agentd.op_run,
        {"images": {}},
        object(),
        io.BytesIO(),
        {"op": "run", "provider": "unknown", "project": "p", "task": "t"},
    )

    assert 'for provider in ("codex", "cursor")' not in source


def main() -> None:
    test_registration_lookup_and_ordering()
    test_builtin_registry_is_fixed_and_fail_closed()
    test_registry_is_not_dynamic_plugin_loading()
    test_broker_uses_registry_for_provider_acceptance()
    print("trusted agent registry regression checks passed")


if __name__ == "__main__":
    main()
