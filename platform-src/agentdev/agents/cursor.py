"""Trusted Cursor driver.

The driver describes Cursor-native state, authentication, version probing,
configuration reconciliation, and run-command semantics. It is declarative
only: Podman construction and project/task lifecycle remain broker/runtime
responsibilities.
"""
from __future__ import annotations

from agentdev.agents.base import (
    AgentCapabilities,
    AgentDriver,
    AuthSpec,
    InstallationSpec,
    ProviderPolicyArtifacts,
    RunSpec,
    VersionProbeSpec,
)
from agentdev.agents.state import JsonFieldReconciliation, ProviderStateAdapter, StateVolumeLayout
from agentdev.core.models import ProviderStateSpec, TaskContext


class CursorDriver(AgentDriver):
    """Current Cursor provider semantics frozen before policy-model migration."""

    def __init__(self) -> None:
        self._state_adapter = ProviderStateAdapter(
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

    def id(self) -> str:
        return "cursor"

    def display_name(self) -> str:
        return "Cursor"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            workspace_modes=frozenset({"readonly", "writable"}),
            interactive_auth=True,
            interactive_run=True,
            native_policy=True,
            native_sandbox=False,
        )

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return self._state_adapter.state_spec()

    def state_adapter(self) -> ProviderStateAdapter:
        return self._state_adapter

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec(
            image_key="cursor",
            containerfile="Containerfile.cursor",
        )

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec(("agent", "--version"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(
            ("agent", "login"),
            environment=(("NO_OPEN_BROWSER", "1"),),
            interactive=True,
            timeout_seconds=900,
        )

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(("agent", "status"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        if not isinstance(policy, dict):
            raise ValueError("Cursor run policy must be a mapping")
        readonly = policy.get("readonly", False)
        outer_only = policy.get("outer_only", False)
        if type(readonly) is not bool or type(outer_only) is not bool:
            raise ValueError("Cursor readonly and outer_only policy values must be boolean")
        if outer_only:
            raise ValueError("Cursor does not support outer-only mode")
        return ProviderPolicyArtifacts()

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        if not isinstance(context, TaskContext):
            raise ValueError("context must be TaskContext")
        if not isinstance(policy, ProviderPolicyArtifacts):
            raise ValueError("policy must be ProviderPolicyArtifacts")
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")

        argv = ("agent", "--trust")
        if prompt.strip():
            argv += (prompt,)
        return RunSpec(argv, interactive=True, policy_artifacts=policy)
