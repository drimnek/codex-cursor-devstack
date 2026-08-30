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
from agentdev.agents.state import JsonFieldReconciliation, ProviderStateAdapter, StatePolicyMount, StateVolumeLayout
from agentdev.core.models import ProviderStateSpec, TaskContext
from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.policy.capabilities import MissingCapabilitiesError, require_policy_capabilities
from agentdev.policy.schema import ExecutionPolicy


CURSOR_RUNTIME_UID = 1000
CURSOR_RUNTIME_GID = 1000
CURSOR_RUNTIME_HOME = "/home/node"
CURSOR_STATE_TARGET = f"{CURSOR_RUNTIME_HOME}/.cursor"
CURSOR_AUTH_TARGET = f"{CURSOR_RUNTIME_HOME}/.config/cursor"
CURSOR_CREDENTIAL_DENY_SEED = "credential-deny.cursorignore"
CURSOR_CREDENTIAL_DENY_TARGET = f"{CURSOR_RUNTIME_HOME}/.cursorignore"
CURSOR_CREDENTIAL_DENY_PATTERNS = (
    "/.cursor/",
    "/.config/cursor/",
)
CURSOR_CONTROL_ISOLATION = RuntimeIsolationRequirements(
    uid=CURSOR_RUNTIME_UID,
    gid=CURSOR_RUNTIME_GID,
)
CURSOR_SANDBOX_ISOLATION = RuntimeIsolationRequirements(
    uid=CURSOR_RUNTIME_UID,
    gid=CURSOR_RUNTIME_GID,
    nested_sandbox_bootstrap=True,
)


class UnsupportedCursorPolicyError(ValueError):
    """Raised when current Cursor policy controls cannot represent a policy safely."""


class CursorDriver(AgentDriver):
    """Current Cursor provider semantics frozen before policy-model migration."""

    def __init__(self) -> None:
        self._state_adapter = ProviderStateAdapter(
            volumes=(
                StateVolumeLayout(
                    key="state",
                    mount=ProviderStateSpec("agent-dev-cursor-state", CURSOR_STATE_TARGET),
                    staging_target="/state",
                    marker=".agent-dev-state-layout-v2",
                    legacy_path=".cursor",
                    empty_error="provider state volume is non-empty but has no layout marker",
                    smoke_marker=".agent-dev-state-write-smoke",
                    owner_uid=CURSOR_RUNTIME_UID,
                    owner_gid=CURSOR_RUNTIME_GID,
                ),
                StateVolumeLayout(
                    key="auth",
                    mount=ProviderStateSpec("agent-dev-cursor-auth", CURSOR_AUTH_TARGET),
                    staging_target="/auth",
                    marker=".agent-dev-auth-layout-v1",
                    legacy_path=".config/cursor",
                    empty_error="Cursor auth state volume is non-empty but has no layout marker",
                    smoke_marker=".agent-dev-auth-write-smoke",
                    owner_uid=CURSOR_RUNTIME_UID,
                    owner_gid=CURSOR_RUNTIME_GID,
                ),
            ),
            legacy_volume="agent-dev-cursor-home",
            # Cursor's Linux native sandbox applies ~/.cursorignore to its
            # sandboxed task filesystem for git-backed workspaces. Keep this
            # broker-owned deny material immutable and separate from the
            # provider-managed cli-config.json state.
            policy_mounts=(
                StatePolicyMount(CURSOR_CREDENTIAL_DENY_SEED, CURSOR_CREDENTIAL_DENY_TARGET, True),
            ),
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
            native_sandbox=True,
            policy_capabilities=frozenset({"provider_state_protection"}),
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
            runtime_isolation=CURSOR_CONTROL_ISOLATION,
        )

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(
            ("agent", "status"),
            interactive=False,
            runtime_isolation=CURSOR_CONTROL_ISOLATION,
        )

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        if isinstance(policy, ExecutionPolicy):
            return self._compile_execution_policy(policy)
        if not isinstance(policy, dict):
            raise ValueError("Cursor run policy must be a mapping or ExecutionPolicy")
        return self._compile_legacy_policy(policy)

    def _compile_legacy_policy(self, policy: dict) -> ProviderPolicyArtifacts:
        """Preserve the frozen readonly/outer-only compatibility path."""
        readonly = policy.get("readonly", False)
        outer_only = policy.get("outer_only", False)
        if type(readonly) is not bool or type(outer_only) is not bool:
            raise ValueError("Cursor readonly and outer_only policy values must be boolean")
        if outer_only:
            raise ValueError("Cursor does not support outer-only mode")
        return ProviderPolicyArtifacts(runtime_isolation=CURSOR_CONTROL_ISOLATION)

    def _compile_execution_policy(self, policy: ExecutionPolicy) -> ProviderPolicyArtifacts:
        """Translate the safely representable subset of platform policy.

        Current Cursor CLI documentation exposes an explicit native sandbox
        switch. POL-007 uses that switch for task-shell-denied execution, but
        intentionally does not claim destination-level egress or provider-state
        confidentiality. Per-run destination allowlists remain SEC-007 work.
        """
        try:
            require_policy_capabilities(policy, self.capabilities(), agent_id=self.id())
        except MissingCapabilitiesError as exc:
            raise UnsupportedCursorPolicyError(str(exc)) from exc

        if policy.workspace.access == "none":
            raise UnsupportedCursorPolicyError(
                "Cursor policy compiler cannot represent workspace.access=none"
            )

        if policy.credentials.provider_auth.task_shell == "deny" and not policy.sandbox.required:
            raise UnsupportedCursorPolicyError(
                "Cursor provider-auth task-shell deny requires provider-native sandboxing"
            )

        network_mode = policy.network.task_shell.mode
        if network_mode == "allowlist":
            raise UnsupportedCursorPolicyError(
                "Cursor per-run task-shell destination allowlists are deferred to MA2-SEC-007"
            )
        if network_mode == "allow":
            if policy.sandbox.required:
                raise UnsupportedCursorPolicyError(
                    "Cursor cannot combine sandbox.required=true with unrestricted task-shell network"
                )
            return ProviderPolicyArtifacts(
                argv=("--sandbox", "disabled"),
                runtime_isolation=CURSOR_CONTROL_ISOLATION,
            )

        # task_shell=deny: enable Cursor's native sandbox. The broker-owned
        # outer workspace mount still supplies the authoritative read-only/read-
        # write workspace boundary. Exact destination-level denial is not
        # advertised until SEC-007/T6 verifies the current Cursor build.
        return ProviderPolicyArtifacts(
            argv=("--sandbox", "enabled"),
            runtime_isolation=CURSOR_SANDBOX_ISOLATION,
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

        argv = ("agent", "--trust", *policy.argv)
        if prompt.strip():
            argv += (prompt,)
        return RunSpec(
            argv,
            interactive=True,
            policy_artifacts=policy,
            runtime_isolation=policy.runtime_isolation,
        )
