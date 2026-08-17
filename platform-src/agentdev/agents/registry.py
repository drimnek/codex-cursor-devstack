"""Trusted in-tree coding-agent registry.

The broker may resolve only driver objects registered by platform code.  This
module deliberately provides no dynamic plugin discovery or user-controlled
module loading.
"""
from __future__ import annotations

from collections.abc import Iterable

from agentdev.agents.base import (
    AgentCapabilities,
    AgentDriver,
    AuthSpec,
    InstallationSpec,
    ProviderPolicyArtifacts,
    RunSpec,
    VersionProbeSpec,
)
from agentdev.agents.state import (
    JsonFieldReconciliation,
    ProviderStateAdapter,
    StatePolicyMount,
    StateVolumeLayout,
)
from agentdev.core.models import ProviderStateSpec, TaskContext
from agentdev.core.validation import is_valid_name


class AgentRegistryError(ValueError):
    """Base class for deterministic trusted-registry validation failures."""


class UnknownAgentError(AgentRegistryError):
    """Requested driver ID is not registered."""


class AgentRegistry:
    """Explicit registry of trusted driver instances supplied by platform code."""

    def __init__(self, drivers: Iterable[AgentDriver] = ()) -> None:
        self._drivers: dict[str, AgentDriver] = {}
        self._frozen = False
        for driver in drivers:
            self.register(driver)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, driver: AgentDriver) -> AgentDriver:
        if self._frozen:
            raise AgentRegistryError("agent registry is frozen")
        if not isinstance(driver, AgentDriver):
            raise AgentRegistryError("registered agent must implement AgentDriver")
        driver_id = driver.id()
        if not is_valid_name(driver_id):
            raise AgentRegistryError("invalid agent id")
        if driver_id in self._drivers:
            raise AgentRegistryError(f"duplicate agent id: {driver_id}")
        self._drivers[driver_id] = driver
        return driver

    def freeze(self) -> "AgentRegistry":
        self._frozen = True
        return self

    def get(self, driver_id: object) -> AgentDriver:
        if not is_valid_name(driver_id):
            raise UnknownAgentError("unsupported provider")
        try:
            return self._drivers[driver_id]
        except KeyError as exc:
            raise UnknownAgentError("unsupported provider") from exc

    def ids(self) -> tuple[str, ...]:
        """Return all registered/enabled driver IDs in deterministic order."""
        return tuple(sorted(self._drivers))

    def drivers(self) -> tuple[AgentDriver, ...]:
        return tuple(self._drivers[driver_id] for driver_id in self.ids())

    def __contains__(self, driver_id: object) -> bool:
        return is_valid_name(driver_id) and driver_id in self._drivers


class _LegacyBrokerDriver(AgentDriver):
    """Identity-only bridge until provider behavior moves in DRV-003/004/005.

    DRV-002 replaces provider enumeration only.  Returning fabricated provider
    semantics here would make the registry look more migrated than it is, so
    every semantic method fails closed until a concrete trusted driver replaces
    this registration.
    """

    def __init__(
        self,
        driver_id: str,
        name: str,
        state_adapter: ProviderStateAdapter,
    ) -> None:
        self._driver_id = driver_id
        self._name = name
        self._state_adapter = state_adapter

    def id(self) -> str:
        return self._driver_id

    def display_name(self) -> str:
        return self._name

    @staticmethod
    def _not_migrated():
        raise NotImplementedError("provider semantics have not moved behind the driver contract yet")

    def capabilities(self) -> AgentCapabilities:
        return self._not_migrated()

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return self._state_adapter.state_spec()

    def state_adapter(self) -> ProviderStateAdapter:
        return self._state_adapter

    def installation_spec(self) -> InstallationSpec:
        return self._not_migrated()

    def version_probe(self) -> VersionProbeSpec:
        return self._not_migrated()

    def auth_spec(self) -> AuthSpec:
        return self._not_migrated()

    def auth_status_spec(self) -> RunSpec:
        return self._not_migrated()

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        return self._not_migrated()

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        return self._not_migrated()


def _codex_state_adapter() -> ProviderStateAdapter:
    return ProviderStateAdapter(
        volumes=(
            StateVolumeLayout(
                key="state",
                mount=ProviderStateSpec("agent-dev-codex-state", "/root/.codex"),
                staging_target="/state",
                marker=".agent-dev-state-layout-v2",
                legacy_path=".codex",
                empty_error="provider state volume is non-empty but has no layout marker",
                smoke_marker=".agent-dev-state-write-smoke",
                cleanup_after_copy=("config.toml",),
            ),
        ),
        legacy_volume="agent-dev-codex-home",
        policy_mounts=(
            StatePolicyMount("config.toml", "/root/.codex/config.toml", True),
        ),
    )


def _cursor_state_adapter() -> ProviderStateAdapter:
    return ProviderStateAdapter(
        volumes=(
            StateVolumeLayout(
                key="state",
                mount=ProviderStateSpec("agent-dev-cursor-state", "/root/.cursor"),
                staging_target="/state",
                marker=".agent-dev-state-layout-v2",
                legacy_path=".cursor",
                empty_error="provider state volume is non-empty but has no layout marker",
                smoke_marker=".agent-dev-state-write-smoke",
            ),
            StateVolumeLayout(
                key="auth",
                mount=ProviderStateSpec("agent-dev-cursor-auth", "/root/.config/cursor"),
                staging_target="/auth",
                marker=".agent-dev-auth-layout-v1",
                legacy_path=".config/cursor",
                empty_error="Cursor auth state volume is non-empty but has no layout marker",
                smoke_marker=".agent-dev-auth-write-smoke",
            ),
        ),
        legacy_volume="agent-dev-cursor-home",
        reconciliation=JsonFieldReconciliation(
            volume_key="state",
            seed_relative_path="cli-config.json",
            state_relative_path="cli-config.json",
            managed_field="permissions",
        ),
    )


def build_builtin_registry() -> AgentRegistry:
    """Build the fixed trusted registry deployed with the current platform."""
    return AgentRegistry(
        (
            _LegacyBrokerDriver("codex", "OpenAI Codex", _codex_state_adapter()),
            _LegacyBrokerDriver("cursor", "Cursor", _cursor_state_adapter()),
        )
    ).freeze()


BUILTIN_AGENT_REGISTRY = build_builtin_registry()
