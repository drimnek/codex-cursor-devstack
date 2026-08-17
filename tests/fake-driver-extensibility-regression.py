#!/usr/bin/env python3
"""Prove a third provider shape works through generic broker/driver contracts."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
from agentdev.agents.registry import AgentRegistry
from agentdev.core.models import ProviderStateSpec, TaskContext


FAKE_ID = "specter"
FAKE_EXE = "specterctl"
FAKE_IMAGE = "agent-dev-specter:test"
FAKE_STATE_VOLUME = "agent-dev-specter-state"
FAKE_STATE_TARGET = "/var/lib/specter/session"


def load_agentd():
    path = PLATFORM / "bin" / "agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_fake_driver_regression", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeExtensibilityDriver(AgentDriver):
    """Test-only provider intentionally unlike either built-in provider."""

    def id(self) -> str:
        return FAKE_ID

    def display_name(self) -> str:
        return "Specter Test Driver"

    def capabilities(self) -> AgentCapabilities:
        # Minimal set needed by the current generic broker run/auth paths.
        return AgentCapabilities(
            workspace_modes=frozenset({"writable"}),
            interactive_auth=True,
            interactive_run=True,
        )

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return (ProviderStateSpec(FAKE_STATE_VOLUME, FAKE_STATE_TARGET),)

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec("specter", "Containerfile.specter")

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec((FAKE_EXE, "build-info", "--short"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(
            (FAKE_EXE, "authenticate", "--terminal"),
            environment=(("SPECTER_AUTH_FLOW", "device"),),
            interactive=True,
            timeout_seconds=123,
        )

    def auth_status_spec(self) -> RunSpec:
        return RunSpec((FAKE_EXE, "identity", "check"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        if not isinstance(policy, dict):
            raise ValueError("fake policy must be a mapping")
        if policy.get("outer_only", False):
            raise ValueError("fake driver has no compatibility modes")
        if policy.get("readonly", False):
            raise ValueError("fake driver supports writable workspace only")
        return ProviderPolicyArtifacts(argv=("--accept-policy", "workspace"))

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
        argv = (FAKE_EXE, "execute", *policy.argv, "--task", context.task)
        if prompt.strip():
            argv += ("--message", prompt)
        return RunSpec(
            argv,
            environment=(("SPECTER_MODE", "broker"),),
            interactive=True,
            policy_artifacts=policy,
        )


@contextlib.contextmanager
def patched(module, **values):
    original = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(module, name, value)


def fake_registry() -> AgentRegistry:
    return AgentRegistry((FakeExtensibilityDriver(),)).freeze()


def config(root: Path) -> dict:
    return {
        "root": str(root),
        "state_dir": str(root / "state"),
        "images": {FAKE_ID: FAKE_IMAGE},
        "limits": {"pids": 64, "memory": "512m", "cpus": "1"},
    }


def test_registry_contract_and_minimal_capabilities() -> None:
    registry = fake_registry()
    assert registry.ids() == (FAKE_ID,)
    driver = registry.get(FAKE_ID)
    assert driver.display_name() == "Specter Test Driver"
    caps = driver.capabilities()
    assert caps.workspace_modes == frozenset({"writable"})
    assert caps.interactive_auth
    assert caps.interactive_run
    assert not caps.native_policy
    assert not caps.native_sandbox
    assert caps.compatibility_modes == frozenset()
    assert driver.state_spec() == (
        ProviderStateSpec(FAKE_STATE_VOLUME, FAKE_STATE_TARGET),
    )
    assert driver.version_probe().argv == (FAKE_EXE, "build-info", "--short")
    assert driver.auth_spec().argv == (FAKE_EXE, "authenticate", "--terminal")


def test_generic_runtime_consumes_arbitrary_state_target() -> None:
    agentd = load_agentd()
    with tempfile.TemporaryDirectory() as td, patched(agentd, AGENT_REGISTRY=fake_registry()):
        args = agentd.common_runtime_args(config(Path(td)), FAKE_ID)
    mount = f"{FAKE_STATE_VOLUME}:{FAKE_STATE_TARGET}:rw"
    assert mount in args
    assert "/root/.codex" not in " ".join(args)
    assert "/root/.cursor" not in " ".join(args)


def test_generic_status_and_version_use_fake_driver_specs() -> None:
    agentd = load_agentd()
    calls: list[list[str]] = []
    output: list[bytes] = []

    def stream(_conn, argv, **_kwargs):
        calls.append(list(argv))
        return 0

    with tempfile.TemporaryDirectory() as td, patched(
        agentd,
        AGENT_REGISTRY=fake_registry(),
        seed_provider_home=lambda _cfg, _provider: None,
        stream_noninteractive=stream,
        send_output=lambda _conn, data: output.append(data),
    ):
        cfg = config(Path(td))
        assert agentd.op_status(cfg, object()) == 0
        assert calls == [[
            "podman", "run", "--rm",
            "--network=slirp4netns:allow_host_loopback=false",
            "--http-proxy=false", "--read-only", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--cpus=1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
            "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
            "-v", f"{FAKE_STATE_VOLUME}:{FAKE_STATE_TARGET}:rw",
            FAKE_IMAGE, FAKE_EXE, "identity", "check",
        ]]
        assert output == [f"\n== {FAKE_ID} ==\n".encode()]

        calls.clear()
        output.clear()
        assert agentd.op_versions(cfg, object()) == 0
        assert calls == [[
            "podman", "run", "--rm", FAKE_IMAGE,
            FAKE_EXE, "build-info", "--short",
        ]]
        assert output == [b"GitNexus intelligence image: not built (optional)\n"]


def test_generic_auth_and_run_consume_fake_specs() -> None:
    agentd = load_agentd()
    auth_calls: list[tuple[list[str], float | None]] = []
    run_calls: list[list[str]] = []
    frames: list[dict] = []

    def interactive_auth(_conn, _fileobj, argv, *, timeout_seconds=None, cidfile=None, **_kwargs):
        auth_calls.append((list(argv), timeout_seconds))
        return 0

    def interactive_run(_conn, _fileobj, argv, **_kwargs):
        run_calls.append(list(argv))
        return 0

    @contextlib.contextmanager
    def no_lock(_pp, _name, _readonly):
        yield

    rec = {
        "mode": "integration",
        "status": "active",
        "base_commit": "0123456789abcdef",
    }
    pp = {
        "tasks": Path("/project/tasks"),
        "agent": Path("/project/repo/agent"),
        "reference": Path("/project/reference"),
    }

    with tempfile.TemporaryDirectory() as td:
        cidfile = Path(td) / "cid"
        cfg = config(Path(td))
        with patched(
            agentd,
            AGENT_REGISTRY=fake_registry(),
            seed_provider_home=lambda _cfg, _provider: None,
            new_interactive_cidfile=lambda _cfg: cidfile,
            add_cidfile=lambda argv, _cidfile: argv,
            send=lambda _conn, frame: frames.append(frame),
            stream_interactive=interactive_auth,
        ):
            assert agentd.op_auth(cfg, object(), io.BytesIO(), FAKE_ID) == 0
        assert auth_calls
        auth_argv, timeout = auth_calls[0]
        assert timeout == 123
        assert ["-e", "SPECTER_AUTH_FLOW=device"] == auth_argv[
            auth_argv.index("-e"):auth_argv.index("-e") + 2
        ]
        assert auth_argv[-4:] == [FAKE_IMAGE, FAKE_EXE, "authenticate", "--terminal"]
        assert frames == [
            {"type": "start", "interactive": True},
            {"type": "exit", "code": 0},
        ]

        frames.clear()
        with patched(
            agentd,
            AGENT_REGISTRY=fake_registry(),
            load_task=lambda _cfg, _project, _task: (rec, pp, Path("/workspace")),
            seed_provider_home=lambda _cfg, _provider: None,
            create_run_execution_plan=lambda cfg, provider, context, run_spec, **kwargs: SimpleNamespace(
                provider=provider,
                image=cfg["images"][provider],
                context=context,
                run_spec=run_spec,
                interaction_mode="interactive",
            ),
            execution_plan_argv=lambda plan: [
                "runtime", plan.provider,
                *[item for name, value in plan.run_spec.environment for item in ("-e", f"{name}={value}")],
                plan.image, *plan.run_spec.argv,
            ],
            new_interactive_cidfile=lambda _cfg: cidfile,
            add_cidfile=lambda argv, _cidfile: argv,
            send=lambda _conn, frame: frames.append(frame),
            stream_interactive=interactive_run,
            lock_one=no_lock,
        ):
            assert agentd.op_run(
                cfg,
                object(),
                io.BytesIO(),
                {
                    "op": "run",
                    "provider": FAKE_ID,
                    "project": "project",
                    "task": "task",
                    "prompt": "implement fake provider proof",
                },
            ) == 0

    assert run_calls
    argv = run_calls[0]
    assert argv[:2] == ["runtime", FAKE_ID]
    assert ["-e", "SPECTER_MODE=broker"] == argv[
        argv.index("SPECTER_MODE=broker") - 1:argv.index("SPECTER_MODE=broker") + 1
    ]
    assert argv[-9:] == [
        FAKE_IMAGE,
        FAKE_EXE,
        "execute",
        "--accept-policy",
        "workspace",
        "--task",
        "task",
        "--message",
        "implement fake provider proof",
    ]
    assert frames == [
        {"type": "start", "interactive": True},
        {"type": "exit", "code": 0},
    ]


def test_fake_provider_is_test_only_and_generic_broker_has_no_provider_commands() -> None:
    production_roots = [
        PLATFORM / "agentdev/broker",
        PLATFORM / "agentdev/core",
        PLATFORM / "agentdev/execution",
        PLATFORM / "agentdev/runtime",
    ]
    for root in production_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert FAKE_EXE not in source, path
            assert FAKE_STATE_VOLUME not in source, path
            assert FAKE_STATE_TARGET not in source, path

    broker_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PLATFORM / "agentdev/broker").glob("*.py")
    )
    forbidden_provider_commands = (
        '("codex", "exec"',
        '["codex", "exec"',
        '("codex", "login"',
        '["codex", "login"',
        '("agent", "--trust"',
        '["agent", "--trust"',
        '("agent", "login"',
        '["agent", "login"',
        '("agent", "status"',
        '["agent", "status"',
        "NO_OPEN_BROWSER",
    )
    for token in forbidden_provider_commands:
        assert token not in broker_source, token

    registry_source = (PLATFORM / "agentdev/agents/registry.py").read_text(encoding="utf-8")
    assert "FakeExtensibilityDriver" not in registry_source
    assert "specter" not in registry_source.lower()


def main() -> None:
    test_registry_contract_and_minimal_capabilities()
    test_generic_runtime_consumes_arbitrary_state_target()
    test_generic_status_and_version_use_fake_driver_specs()
    test_generic_auth_and_run_consume_fake_specs()
    test_fake_provider_is_test_only_and_generic_broker_has_no_provider_commands()
    print("fake driver extensibility regression checks passed")


if __name__ == "__main__":
    main()
