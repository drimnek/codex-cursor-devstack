#!/usr/bin/env python3
"""Freeze the provider-neutral AgentDriver contract before provider migration."""
from __future__ import annotations

import dataclasses
import inspect
import json
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
    PolicyFileSpec,
    ProviderPolicyArtifacts,
    RunSpec,
    VersionProbeSpec,
)
from agentdev.core.models import ProviderStateSpec, TaskContext


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}")


def sample_context() -> TaskContext:
    return TaskContext(
        project="demo",
        task="REQ-1",
        mode="sequential",
        status="active",
        metadata_path=Path("/srv/agent-dev/projects/demo/tasks/REQ-1.json"),
        workspace=Path("/srv/agent-dev/projects/demo/repo/agent"),
        record={"base_commit": "0" * 40},
    )


class ReferenceDriver(AgentDriver):
    """Configurable fake used to prove the interface without migrating providers."""

    def __init__(
        self,
        *,
        driver_id: str,
        name: str,
        capabilities: AgentCapabilities,
        state: tuple[ProviderStateSpec, ...],
        install: InstallationSpec,
        version: VersionProbeSpec,
        auth: AuthSpec,
        auth_status: RunSpec,
        run_prefix: tuple[str, ...],
        policy_file: PolicyFileSpec | None = None,
    ) -> None:
        self._id = driver_id
        self._name = name
        self._capabilities = capabilities
        self._state = state
        self._install = install
        self._version = version
        self._auth = auth
        self._auth_status = auth_status
        self._run_prefix = run_prefix
        self._policy_file = policy_file

    def id(self) -> str:
        return self._id

    def display_name(self) -> str:
        return self._name

    def capabilities(self) -> AgentCapabilities:
        return self._capabilities

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return self._state

    def installation_spec(self) -> InstallationSpec:
        return self._install

    def version_probe(self) -> VersionProbeSpec:
        return self._version

    def auth_spec(self) -> AuthSpec:
        return self._auth

    def auth_status_spec(self) -> RunSpec:
        return self._auth_status

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        assert isinstance(policy, dict)
        argv = tuple(policy.get("argv", ()))
        files = () if self._policy_file is None else (self._policy_file,)
        return ProviderPolicyArtifacts(files=files, argv=argv)

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        assert context.status in {"active", "completed", "merged"}
        argv = (*self._run_prefix, *policy.argv)
        if prompt.strip():
            argv += (prompt,)
        return RunSpec(argv=argv, interactive=True, policy_artifacts=policy)


def test_model_validation_and_serialization() -> None:
    capabilities = AgentCapabilities(
        workspace_modes=frozenset({"readonly", "writable"}),
        interactive_auth=True,
        interactive_run=True,
        native_policy=True,
        native_sandbox=True,
        compatibility_modes=frozenset({"compatibility"}),
    )
    assert capabilities.as_dict() == {
        "workspace_modes": ["readonly", "writable"],
        "interactive_auth": True,
        "interactive_run": True,
        "native_policy": True,
        "native_sandbox": True,
        "compatibility_modes": ["compatibility"],
    }

    state = ProviderStateSpec("state-volume", "/root/.provider")
    assert state.target == "/root/.provider"
    assert state.as_dict() == {
        "source": "state-volume",
        "target": "/root/.provider",
        "read_only": False,
    }
    expect(ValueError, ProviderStateSpec, "state-volume", "relative/path")
    expect(ValueError, ProviderStateSpec, "", "/root/.provider")

    policy = ProviderPolicyArtifacts(
        files=(PolicyFileSpec("/seed/policy", "/root/.provider/policy", True),),
        argv=("--policy", "strict"),
        environment=(("PROVIDER_MODE", "strict"),),
    )
    run = RunSpec(("provider-cli", "run"), (("RUN_MODE", "test"),), True, policy)
    auth = AuthSpec(("provider-cli", "login"), (("NO_BROWSER", "1"),), True, 900)
    version = VersionProbeSpec(("provider-cli", "--version"))
    install = InstallationSpec(
        "provider",
        "Containerfile.provider",
        "provider",
        (("PROVIDER_VERSION", "provider"),),
    )

    for payload in (
        state.as_dict(),
        policy.as_dict(),
        run.as_dict(),
        auth.as_dict(),
        version.as_dict(),
        install.as_dict(),
    ):
        json.dumps(payload)

    expect(ValueError, RunSpec, ())
    expect(ValueError, RunSpec, ("provider-cli",), (("DUP", "1"), ("DUP", "2")))
    expect(ValueError, PolicyFileSpec, "/seed/policy", "relative/policy")
    expect(ValueError, AgentCapabilities, frozenset({"unknown"}))
    expect(ValueError, AgentCapabilities, frozenset())

    for value in (capabilities, state, policy, run, auth, version, install):
        assert dataclasses.is_dataclass(value)
        expect(dataclasses.FrozenInstanceError, setattr, value, next(iter(value.__dataclass_fields__)), None)


def test_agent_driver_abstract_contract() -> None:
    expected = {
        "id",
        "display_name",
        "capabilities",
        "state_spec",
        "installation_spec",
        "version_probe",
        "auth_spec",
        "auth_status_spec",
        "compile_policy",
        "create_run_spec",
    }
    assert inspect.isabstract(AgentDriver)
    assert expected <= set(AgentDriver.__abstractmethods__)

    class IncompleteDriver(AgentDriver):
        pass

    expect(TypeError, IncompleteDriver)


def test_current_provider_shapes_fit_generic_contract() -> None:
    # Shape A represents the current provider that uses one state volume,
    # a mounted native policy file, a version-pinned build argument, and an
    # explicit compatibility execution mode.
    shape_a = ReferenceDriver(
        driver_id="shape-a",
        name="Reference A",
        capabilities=AgentCapabilities(
            workspace_modes=frozenset({"readonly", "writable"}),
            interactive_auth=True,
            interactive_run=True,
            native_policy=True,
            native_sandbox=True,
            compatibility_modes=frozenset({"outer-only"}),
        ),
        state=(ProviderStateSpec("agent-dev-shape-a-state", "/root/.shape-a"),),
        install=InstallationSpec(
            "shape-a",
            "Containerfile.shape-a",
            "shape-a",
            (("CLI_VERSION", "shape-a"),),
        ),
        version=VersionProbeSpec(("shape-a-cli", "--version")),
        auth=AuthSpec(("shape-a-cli", "login", "--device-auth"), timeout_seconds=900),
        auth_status=RunSpec(("shape-a-cli", "login", "status"), interactive=False),
        run_prefix=("shape-a-cli", "exec"),
        policy_file=PolicyFileSpec("/seed/config", "/root/.shape-a/config", True),
    )
    policy_a = shape_a.compile_policy(
        {"argv": ("--sandbox", "workspace-write", "-c", "approval_policy=never")}
    )
    run_a = shape_a.create_run_spec(sample_context(), policy_a, "implement")
    assert len(shape_a.state_spec()) == 1
    assert run_a.argv == (
        "shape-a-cli",
        "exec",
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        "implement",
    )
    assert run_a.policy_artifacts.files[0].read_only

    # Shape B represents the current provider that needs separate CLI and XDG
    # authentication state, an environment-controlled login flow, and no
    # additional mounted policy file at invocation time.
    shape_b = ReferenceDriver(
        driver_id="shape-b",
        name="Reference B",
        capabilities=AgentCapabilities(
            workspace_modes=frozenset({"readonly", "writable"}),
            interactive_auth=True,
            interactive_run=True,
            native_policy=True,
            native_sandbox=False,
        ),
        state=(
            ProviderStateSpec("agent-dev-shape-b-state", "/root/.shape-b"),
            ProviderStateSpec("agent-dev-shape-b-auth", "/root/.config/shape-b"),
        ),
        install=InstallationSpec("shape-b", "Containerfile.shape-b"),
        version=VersionProbeSpec(("shape-b-cli", "--version")),
        auth=AuthSpec(("shape-b-cli", "login"), (("NO_OPEN_BROWSER", "1"),), True, 900),
        auth_status=RunSpec(("shape-b-cli", "status"), interactive=False),
        run_prefix=("shape-b-cli", "--trust"),
    )
    policy_b = shape_b.compile_policy({"argv": ()})
    run_b = shape_b.create_run_spec(sample_context(), policy_b, "review")
    assert len(shape_b.state_spec()) == 2
    assert shape_b.auth_spec().environment == (("NO_OPEN_BROWSER", "1"),)
    assert run_b.argv == ("shape-b-cli", "--trust", "review")
    assert run_b.policy_artifacts.files == ()


def test_generic_contract_has_no_provider_identity_fields() -> None:
    generic_classes = (
        AgentCapabilities,
        InstallationSpec,
        PolicyFileSpec,
        ProviderPolicyArtifacts,
        AuthSpec,
        VersionProbeSpec,
        RunSpec,
        ProviderStateSpec,
    )
    for cls in generic_classes:
        field_names = {field.name.lower() for field in dataclasses.fields(cls)}
        assert not any("codex" in name or "cursor" in name for name in field_names), (cls, field_names)

    source = (PLATFORM / "agentdev/agents/base.py").read_text(encoding="utf-8").lower()
    assert "podman" not in source
    assert "subprocess" not in source
    assert "socket." not in source
    assert "repo/agent" not in source
    assert "worktree" not in source


def main() -> None:
    test_model_validation_and_serialization()
    test_agent_driver_abstract_contract()
    test_current_provider_shapes_fit_generic_contract()
    test_generic_contract_has_no_provider_identity_fields()
    print("agent driver contract regression checks passed")


if __name__ == "__main__":
    main()
