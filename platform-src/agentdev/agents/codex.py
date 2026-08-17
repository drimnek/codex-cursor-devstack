"""Trusted OpenAI Codex driver.

The driver describes Codex-native state, authentication, version probing,
policy arguments, and run-command semantics.  It is declarative only: Podman
construction and project/task lifecycle remain broker/runtime responsibilities.
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
from agentdev.agents.state import ProviderStateAdapter, StatePolicyMount, StateVolumeLayout
from agentdev.core.models import ProviderStateSpec, TaskContext


class CodexDriver(AgentDriver):
    """Current Codex provider semantics frozen before policy-model migration."""

    def __init__(self) -> None:
        self._state_adapter = ProviderStateAdapter(
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

    def id(self) -> str:
        return "codex"

    def display_name(self) -> str:
        return "OpenAI Codex"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            workspace_modes=frozenset({"readonly", "writable"}),
            interactive_auth=True,
            interactive_run=True,
            native_policy=True,
            native_sandbox=True,
            compatibility_modes=frozenset({"outer-only"}),
        )

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return self._state_adapter.state_spec()

    def state_adapter(self) -> ProviderStateAdapter:
        return self._state_adapter

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec(
            image_key="codex",
            containerfile="Containerfile.codex",
            version_key="codex",
        )

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec(("codex", "--version"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(
            ("codex", "login", "--device-auth"),
            interactive=True,
            timeout_seconds=900,
        )

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(("codex", "login", "status"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        if not isinstance(policy, dict):
            raise ValueError("Codex run policy must be a mapping")
        readonly = policy.get("readonly", False)
        outer_only = policy.get("outer_only", False)
        if type(readonly) is not bool or type(outer_only) is not bool:
            raise ValueError("Codex readonly and outer_only policy values must be boolean")

        if outer_only:
            sandbox = "danger-full-access"
        elif readonly:
            sandbox = "read-only"
        else:
            sandbox = "workspace-write"

        return ProviderPolicyArtifacts(
            argv=("--sandbox", sandbox, "-c", "approval_policy=never"),
        )

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

        argv = ("codex", "exec", *policy.argv)
        if prompt.strip():
            argv += (prompt,)
        return RunSpec(argv, interactive=True, policy_artifacts=policy)
