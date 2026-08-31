"""Trusted OpenAI Codex driver.

The driver describes Codex-native state, authentication, version probing,
policy arguments, and run-command semantics.  It is declarative only: Podman
construction and project/task lifecycle remain broker/runtime responsibilities.
"""
from __future__ import annotations

import json

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
from agentdev.execution.isolation import RuntimeIsolationRequirements
from agentdev.policy.capabilities import MissingCapabilitiesError, require_policy_capabilities
from agentdev.policy.schema import ExecutionPolicy


CODEX_POLICY_COMPILER_BASELINE = "0.147.0"
CODEX_CREDENTIAL_PERMISSION_PROFILE = "agentdev_credential_confidentiality"
CODEX_PROVIDER_STATE_TARGET = "/home/node/.codex"
CODEX_RUNTIME_UID = 1000
CODEX_RUNTIME_GID = 1000
CODEX_CONTROL_ISOLATION = RuntimeIsolationRequirements(
    uid=CODEX_RUNTIME_UID,
    gid=CODEX_RUNTIME_GID,
)
CODEX_SANDBOX_ISOLATION = RuntimeIsolationRequirements(
    uid=CODEX_RUNTIME_UID,
    gid=CODEX_RUNTIME_GID,
    nested_sandbox_bootstrap=True,
)


def codex_credential_confidentiality_config_argv(workspace_access: str) -> tuple[str, ...]:
    """Return pinned Codex controls that hide provider state from task shells.

    SEC-002 uses the same configuration in normal policy-based native-sandbox
    execution and in the deployed T5/T6 proof. Legacy compatibility execution
    remains unchanged and does not claim this guarantee.
    """
    if workspace_access == "read":
        base_profile = ":read-only"
    elif workspace_access == "write":
        base_profile = ":workspace"
    else:
        raise ValueError("Codex credential probe workspace access must be read or write")

    profile = CODEX_CREDENTIAL_PERMISSION_PROFILE
    return (
        "-c",
        f'default_permissions="{profile}"',
        "-c",
        f'permissions.{profile}.extends="{base_profile}"',
        "-c",
        (
            f'permissions.{profile}.filesystem={{ '
            f'"{CODEX_PROVIDER_STATE_TARGET}" = "deny", '
            f'"{CODEX_PROVIDER_STATE_TARGET}/**" = "deny" }}'
        ),
        "-c",
        'shell_environment_policy.inherit="all"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=false",
        "-c",
        'history.persistence="none"',
    )


class UnsupportedCodexPolicyError(ValueError):
    """Raised when the pinned Codex integration cannot represent a policy safely."""


def _toml_domain_allowlist(destinations: tuple[str, ...]) -> str:
    entries = ", ".join(
        f'{json.dumps(destination, ensure_ascii=False)} = "allow"'
        for destination in destinations
    )
    return f"features.network_proxy.domains={{ {entries} }}"


def codex_task_egress_config_argv(
    mode: str,
    destinations: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return pinned Codex task-shell network controls for SEC-006."""
    if mode == "deny":
        if destinations:
            raise ValueError("Codex network deny does not accept destinations")
        return ("-c", "sandbox_workspace_write.network_access=false")
    if mode != "allowlist":
        raise ValueError("Codex task egress mode must be deny or allowlist")
    if not destinations:
        raise ValueError("Codex network allowlist requires destinations")

    return (
        "-c",
        "sandbox_workspace_write.network_access=true",
        "-c",
        'projects={ "/workspace" = { trust_level = "untrusted" } }',
        "-c",
        "features.network_proxy.enabled=true",
        "-c",
        "features.network_proxy.enable_socks5=false",
        "-c",
        "features.network_proxy.enable_socks5_udp=false",
        "-c",
        "features.network_proxy.allow_upstream_proxy=false",
        "-c",
        "features.network_proxy.dangerously_allow_non_loopback_proxy=false",
        "-c",
        "features.network_proxy.dangerously_allow_all_unix_sockets=false",
        "-c",
        'features.network_proxy.mode="full"',
        "-c",
        "features.network_proxy.allow_local_binding=false",
        "-c",
        _toml_domain_allowlist(destinations),
    )


class CodexDriver(AgentDriver):
    """Current Codex provider semantics frozen before policy-model migration."""

    def __init__(self) -> None:
        self._state_adapter = ProviderStateAdapter(
            volumes=(
                StateVolumeLayout(
                    key="state",
                    mount=ProviderStateSpec("agent-dev-codex-state", CODEX_PROVIDER_STATE_TARGET),
                    staging_target="/state",
                    marker=".agent-dev-state-layout-v2",
                    legacy_path=".codex",
                    empty_error="provider state volume is non-empty but has no layout marker",
                    smoke_marker=".agent-dev-state-write-smoke",
                    cleanup_after_copy=("config.toml",),
                    owner_uid=CODEX_RUNTIME_UID,
                    owner_gid=CODEX_RUNTIME_GID,
                ),
            ),
            legacy_volume="agent-dev-codex-home",
            policy_mounts=(
                StatePolicyMount(
                    "config.toml", f"{CODEX_PROVIDER_STATE_TARGET}/config.toml", True
                ),
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
            policy_capabilities=frozenset({"provider_state_protection"}),
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
            runtime_isolation=CODEX_CONTROL_ISOLATION,
        )

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(
            ("codex", "login", "status"),
            interactive=False,
            runtime_isolation=CODEX_CONTROL_ISOLATION,
        )

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        if isinstance(policy, ExecutionPolicy):
            return self._compile_execution_policy(policy)
        if not isinstance(policy, dict):
            raise ValueError("Codex run policy must be a mapping or ExecutionPolicy")
        return self._compile_legacy_policy(policy)

    def _compile_legacy_policy(self, policy: dict) -> ProviderPolicyArtifacts:
        """Preserve the frozen readonly/outer-only invocation contract."""
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

        runtime_isolation = (
            CODEX_CONTROL_ISOLATION if outer_only else CODEX_SANDBOX_ISOLATION
        )
        return ProviderPolicyArtifacts(
            argv=("--sandbox", sandbox, "-c", "approval_policy=never"),
            runtime_isolation=runtime_isolation,
        )

    def _compile_execution_policy(self, policy: ExecutionPolicy) -> ProviderPolicyArtifacts:
        """Translate provider-neutral policy into pinned Codex CLI controls.

        The compiler targets the pinned 0.147.0 integration baseline. It emits
        Codex-native sandbox/approval/network controls, but does not advertise
        hardened support: credential confidentiality and destination-level
        enforcement remain subject to the later T6 security contract.
        """
        try:
            require_policy_capabilities(policy, self.capabilities(), agent_id=self.id())
        except MissingCapabilitiesError as exc:
            raise UnsupportedCodexPolicyError(str(exc)) from exc

        if policy.workspace.access == "none":
            raise UnsupportedCodexPolicyError(
                "Codex policy compiler cannot represent workspace.access=none"
            )
        sandbox = "read-only" if policy.workspace.access == "read" else "workspace-write"
        argv: list[str] = ["--sandbox", sandbox, "-c", "approval_policy=never"]

        network = policy.network.task_shell
        if sandbox == "read-only":
            if network.mode != "deny":
                raise UnsupportedCodexPolicyError(
                    "Codex read-only sandbox cannot enable task-shell network access"
                )
        elif network.mode == "deny":
            argv += list(codex_task_egress_config_argv("deny"))
        elif network.mode == "allow":
            argv += [
                "-c",
                "sandbox_workspace_write.network_access=true",
                "-c",
                "features.network_proxy.enabled=false",
            ]
        elif network.mode == "allowlist":
            argv += list(
                codex_task_egress_config_argv("allowlist", network.destinations)
            )
        else:
            raise UnsupportedCodexPolicyError(
                f"unsupported Codex task-shell network mode: {network.mode}"
            )

        if policy.credentials.provider_auth.task_shell == "deny":
            if not policy.sandbox.required:
                raise UnsupportedCodexPolicyError(
                    "Codex provider-auth task-shell deny requires provider-native sandboxing"
                )
            argv += list(
                codex_credential_confidentiality_config_argv(policy.workspace.access)
            )

        runtime_isolation = (
            CODEX_SANDBOX_ISOLATION
            if policy.sandbox.required
            else CODEX_CONTROL_ISOLATION
        )
        return ProviderPolicyArtifacts(
            argv=tuple(argv),
            runtime_isolation=runtime_isolation,
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
        return RunSpec(
            argv,
            interactive=True,
            policy_artifacts=policy,
            runtime_isolation=policy.runtime_isolation,
        )
