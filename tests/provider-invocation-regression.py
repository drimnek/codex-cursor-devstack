#!/usr/bin/env python3
"""Freeze current Codex/Cursor invocation semantics before AgentDriver extraction."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_agentd():
    path = ROOT / "platform-src/bin/agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_provider_invocation", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.LOG.disabled = True
    return module


agentd = load_agentd()


class FakeConn:
    def __init__(self):
        self.sent = bytearray()
        self.fileobj = io.BytesIO()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)


class RunResult:
    def __init__(self, returncode: int):
        self.returncode = returncode


def mount_specs(args: list[str]) -> list[str]:
    return [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-v"]


def make_cfg(root: Path) -> dict:
    seed = root / "platform" / "seed"
    (seed / "codex").mkdir(parents=True)
    (seed / "cursor").mkdir(parents=True)
    (root / "platform" / "projects").mkdir(parents=True)
    (root / "tmp").mkdir(parents=True)
    (seed / "codex" / "config.toml").write_text(
        'approval_policy = "never"\n',
        encoding="utf-8",
    )
    (seed / "cursor" / "credential-deny.cursorignore").write_text(
        "/.cursor/\n/.config/cursor/\n",
        encoding="utf-8",
    )
    return {
        "root": str(root),
        "state_dir": str(root / "state"),
        "images": {
            "base": "base-image",
            "codex": "codex-image",
            "cursor": "cursor-image",
        },
        "limits": {
            "pids": 256,
            "memory": "4g",
            "cpus": "2",
        },
    }


def check_runtime_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg = make_cfg(Path(td))

        codex = agentd.common_runtime_args(cfg, "codex")
        cursor = agentd.common_runtime_args(cfg, "cursor")
        codex_offline = agentd.common_runtime_args(
            cfg,
            "codex",
            network_enabled=False,
        )

        for args in (codex, cursor):
            assert args[:3] == ["podman", "run", "--rm"]
            assert f"--network={agentd.PROVIDER_NETWORK_MODE}" in args
            assert "--http-proxy=false" in args
            assert "--read-only" in args
            assert "--cap-drop=all" in args
            assert "--security-opt=no-new-privileges" in args
            assert "--pids-limit=256" in args
            assert "--memory=4g" in args
            assert "--cpus=2" in args
            assert "/tmp:rw,nosuid,nodev,size=512m" in args
            assert "/run:rw,nosuid,nodev,size=64m" in args

        assert "--network=none" in codex_offline
        assert f"--network={agentd.PROVIDER_NETWORK_MODE}" not in codex_offline

        codex_mounts = mount_specs(codex)
        assert "agent-dev-codex-state:/home/node/.codex:rw" in codex_mounts
        assert any(
            spec.endswith(":/home/node/.codex/config.toml:ro")
            for spec in codex_mounts
        )
        assert "agent-dev-cursor-auth:/home/node/.config/cursor:rw" not in codex_mounts

        cursor_mounts = mount_specs(cursor)
        assert "agent-dev-cursor-state:/home/node/.cursor:rw" in cursor_mounts
        assert "agent-dev-cursor-auth:/home/node/.config/cursor:rw" in cursor_mounts
        assert any(
            spec.endswith(":/home/node/.cursorignore:ro") for spec in cursor_mounts
        )
        assert not any(
            spec.endswith(":/home/node/.codex/config.toml:ro")
            for spec in cursor_mounts
        )


def check_auth_invocations() -> None:
    originals = {
        "seed_provider_home": agentd.seed_provider_home,
        "common_runtime_args": agentd.common_runtime_args,
        "execute_runtime_argv": agentd.execute_runtime_argv,
    }
    seeded: list[str] = []
    calls: list[tuple[list[str], dict]] = []

    try:
        agentd.seed_provider_home = lambda _cfg, provider: seeded.append(provider)
        agentd.common_runtime_args = (
            lambda _cfg, provider, **_kwargs:
            ["podman", "run", "--rm", f"--provider={provider}"]
        )
        def fake_execute(_cfg, _conn, _fileobj, argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            return 0

        agentd.execute_runtime_argv = fake_execute
        cfg = {
            "images": {
                "codex": "codex-image",
                "cursor": "cursor-image",
            }
        }

        codex_conn = FakeConn()
        assert agentd.op_auth(
            cfg,
            codex_conn,
            codex_conn.fileobj,
            "codex",
        ) == 0
        codex_argv, codex_kwargs = calls[-1]
        assert codex_argv == [
            "podman", "run", "--rm", "--provider=codex",
            "codex-image", "codex", "login", "--device-auth",
        ]
        assert codex_kwargs["interactive"] is True
        assert codex_kwargs["timeout_seconds"] == agentd.AUTH_TIMEOUT_SECONDS

        cursor_conn = FakeConn()
        assert agentd.op_auth(
            cfg,
            cursor_conn,
            cursor_conn.fileobj,
            "cursor",
        ) == 0
        cursor_argv, cursor_kwargs = calls[-1]
        assert cursor_argv == [
            "podman", "run", "--rm", "--provider=cursor",
            "-e", "NO_OPEN_BROWSER=1",
            "cursor-image", "agent", "login",
        ]
        assert cursor_kwargs["interactive"] is True
        assert cursor_kwargs["timeout_seconds"] == agentd.AUTH_TIMEOUT_SECONDS
        assert seeded == ["codex", "cursor"]
    finally:
        for name, value in originals.items():
            setattr(agentd, name, value)


def check_status_invocations() -> None:
    originals = {
        "seed_provider_home": agentd.seed_provider_home,
        "common_runtime_args": agentd.common_runtime_args,
        "stream_noninteractive": agentd.stream_noninteractive,
    }
    seeded: list[str] = []
    calls: list[list[str]] = []

    try:
        agentd.seed_provider_home = lambda _cfg, provider: seeded.append(provider)
        agentd.common_runtime_args = (
            lambda _cfg, provider, **_kwargs:
            ["podman", "run", "--rm", f"--provider={provider}"]
        )
        agentd.stream_noninteractive = (
            lambda _conn, argv, **_kwargs: calls.append(list(argv)) or 0
        )

        cfg = {
            "images": {
                "codex": "codex-image",
                "cursor": "cursor-image",
            }
        }
        assert agentd.op_status(cfg, FakeConn()) == 0
        assert calls == [
            [
                "podman", "run", "--rm", "--provider=codex",
                "codex-image", "codex", "login", "status",
            ],
            [
                "podman", "run", "--rm", "--provider=cursor",
                "cursor-image", "agent", "status",
            ],
        ]
        assert seeded == ["codex", "cursor"]
    finally:
        for name, value in originals.items():
            setattr(agentd, name, value)


def check_version_invocations() -> None:
    original = agentd.stream_noninteractive
    calls: list[list[str]] = []
    try:
        agentd.stream_noninteractive = (
            lambda _conn, argv, **_kwargs: calls.append(list(argv)) or 0
        )
        cfg = {
            "images": {
                "codex": "codex-image",
                "cursor": "cursor-image",
            }
        }
        assert agentd.op_versions(cfg, FakeConn()) == 0
        assert calls == [
            [
                "podman", "run", "--rm",
                "codex-image", "codex", "--version",
            ],
            [
                "podman", "run", "--rm",
                "cursor-image", "agent", "--version",
            ],
        ]
    finally:
        agentd.stream_noninteractive = original


def check_smoke_invocations() -> None:
    originals = {
        "seed_provider_home": agentd.seed_provider_home,
        "common_runtime_args": agentd.common_runtime_args,
        "stream_noninteractive": agentd.stream_noninteractive,
        "run": agentd.subprocess.run,
    }
    seeded: list[str] = []
    runtime_calls: list[tuple[str, bool]] = []
    streams: list[list[str]] = []

    with tempfile.TemporaryDirectory() as td:
        cfg = make_cfg(Path(td))

        try:
            agentd.seed_provider_home = (
                lambda _cfg, provider: seeded.append(provider)
            )

            def fake_runtime(
                _cfg,
                provider,
                _workspace=None,
                *,
                network_enabled=True,
                **_kwargs,
            ):
                runtime_calls.append((provider, network_enabled))
                mode = "provider-network" if network_enabled else "network-none"
                return [
                    "podman", "run", "--rm",
                    f"--provider={provider}",
                    f"--mode={mode}",
                ]

            agentd.common_runtime_args = fake_runtime
            agentd.stream_noninteractive = (
                lambda _conn, argv, **_kwargs:
                streams.append(list(argv)) or 0
            )

            def fake_run(argv, **_kwargs):
                argv = list(argv)
                if argv[:3] == ["podman", "image", "exists"]:
                    return RunResult(0)
                joined = " ".join(map(str, argv))
                if "host.containers.internal" in joined:
                    return RunResult(1)
                if "echo bad >> /workspace/marker" in joined:
                    return RunResult(1)
                return RunResult(0)

            agentd.subprocess.run = fake_run

            assert agentd.op_smoke(cfg, FakeConn()) == 0
            assert seeded == ["codex", "cursor"]
            assert runtime_calls == [
                ("codex", False),
                ("cursor", False),
                ("codex", True),
            ]

            codex_state = next(
                argv for argv in streams
                if "--provider=codex" in argv
                and "--mode=network-none" in argv
            )
            assert "/home/node/.codex/.agent-dev-state-write-smoke" in codex_state[-1]
            assert "/home/node/.config/cursor/.agent-dev-auth-write-smoke" not in codex_state[-1]

            cursor_state = next(
                argv for argv in streams
                if "--provider=cursor" in argv
                and "--mode=network-none" in argv
            )
            assert "/home/node/.cursor/.agent-dev-state-write-smoke" in cursor_state[-1]
            assert "/home/node/.config/cursor/.agent-dev-auth-write-smoke" in cursor_state[-1]

            provider_network = next(
                argv for argv in streams
                if "--provider=codex" in argv
                and "--mode=provider-network" in argv
            )
            assert provider_network[-3:] == ["bash", "-lc", "true"]
        finally:
            agentd.seed_provider_home = originals["seed_provider_home"]
            agentd.common_runtime_args = originals["common_runtime_args"]
            agentd.stream_noninteractive = originals["stream_noninteractive"]
            agentd.subprocess.run = originals["run"]


def check_run_invocations() -> None:
    originals = {
        "load_task": agentd.load_task,
        "seed_provider_home": agentd.seed_provider_home,
        "create_run_execution_plan": agentd.create_run_execution_plan,
        "execution_plan_argv": agentd.execution_plan_argv,
        "execute_runtime_plan": agentd.execute_runtime_plan,
        "lock_one": agentd.lock_one,
    }

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = root / "tasks"
        reference = root / "reference"
        agent_repo = root / "repo-agent"
        workspace = root / "workspace"
        for path in (tasks, reference, agent_repo, workspace):
            path.mkdir(parents=True)

        meta = tasks / "task.json"
        meta.write_text("{}\n", encoding="utf-8")

        record = {
            "mode": "integration",
            "status": "active",
            "base_commit": "0123456789abcdef",
        }
        pp = {
            "tasks": tasks,
            "reference": reference,
            "agent": agent_repo,
        }
        plan_calls: list[dict] = []
        invocations: list[list[str]] = []
        seeded: list[str] = []

        try:
            agentd.load_task = (
                lambda _cfg, _project, _task:
                (dict(record), pp, workspace)
            )
            agentd.seed_provider_home = (
                lambda _cfg, provider: seeded.append(provider)
            )

            def fake_create_plan(
                cfg, provider, context, run_spec, *, readonly, outer_only, reference, git_common
            ):
                required_capabilities = {
                    "workspace:readonly" if readonly else "workspace:writable",
                    "interactive-run",
                }
                if outer_only:
                    required_capabilities.add("compatibility:outer-only")
                plan = SimpleNamespace(
                    provider=provider,
                    context=context,
                    run_spec=run_spec,
                    readonly=readonly,
                    outer_only=outer_only,
                    reference=reference,
                    git_common=git_common,
                    image=cfg["images"][provider],
                    interaction_mode="interactive",
                    required_capabilities=frozenset(required_capabilities),
                )
                plan_calls.append(plan)
                return plan

            def fake_plan_argv(plan):
                context = plan.context
                return [
                    "podman", "run", "--rm", f"--provider={plan.provider}",
                    "-e", f"AGENT_TASK_ID={context.task}",
                    "-e", f"AGENT_TASK_MODE={context.mode}",
                    "-e", f"AGENT_TASK_BASE_COMMIT={context.record['base_commit']}",
                    plan.image, *plan.run_spec.argv,
                ]

            agentd.create_run_execution_plan = fake_create_plan
            agentd.execution_plan_argv = fake_plan_argv
            agentd.execute_runtime_plan = (
                lambda _cfg, _conn, _fileobj, plan:
                invocations.append(fake_plan_argv(plan)) or 0
            )
            agentd.lock_one = lambda *_args, **_kwargs: contextlib.nullcontext()

            cfg = {
                "images": {
                    "codex": "codex-image",
                    "cursor": "cursor-image",
                }
            }

            def run(provider: str, *, readonly=False, outer_only=False, prompt=""):
                conn = FakeConn()
                rc = agentd.op_run(
                    cfg,
                    conn,
                    conn.fileobj,
                    {
                        "op": "run",
                        "provider": provider,
                        "project": "project",
                        "task": "task",
                        "readonly": readonly,
                        "outer_only": outer_only,
                        "prompt": prompt,
                    },
                )
                assert rc == 0
                return invocations[-1], plan_calls[-1]

            argv, runtime = run("codex")
            assert argv[-7:] == [
                "codex-image",
                "codex", "exec",
                "--sandbox", "workspace-write",
                "-c", "approval_policy=never",
            ]
            assert runtime.readonly is False

            argv, runtime = run("codex", readonly=True)
            assert argv[-7:] == [
                "codex-image",
                "codex", "exec",
                "--sandbox", "read-only",
                "-c", "approval_policy=never",
            ]
            assert runtime.readonly is True

            argv, _runtime = run("codex", outer_only=True)
            assert argv[-7:] == [
                "codex-image",
                "codex", "exec",
                "--sandbox", "danger-full-access",
                "-c", "approval_policy=never",
            ]

            argv, _runtime = run("codex", prompt="review this")
            assert argv[-1] == "review this"

            argv, runtime = run("cursor")
            assert argv[-3:] == ["cursor-image", "agent", "--trust"]
            assert runtime.readonly is False

            argv, runtime = run("cursor", readonly=True, prompt="review this")
            assert argv[-4:] == [
                "cursor-image", "agent", "--trust", "review this",
            ]
            assert runtime.readonly is True

            for argv in invocations:
                assert "-e" in argv
                assert "AGENT_TASK_ID=task" in argv
                assert "AGENT_TASK_MODE=integration" in argv
                assert "AGENT_TASK_BASE_COMMIT=0123456789abcdef" in argv

            for plan in plan_calls:
                assert plan.context.workspace == workspace
                assert plan.context.metadata_path == meta
                assert plan.reference == reference
                assert plan.git_common is None

            assert seeded == [
                "codex",
                "codex",
                "codex",
                "codex",
                "cursor",
                "cursor",
            ]
        finally:
            for name, value in originals.items():
                setattr(agentd, name, value)


def main() -> None:
    check_runtime_envelope()
    check_auth_invocations()
    check_status_invocations()
    check_version_invocations()
    check_smoke_invocations()
    check_run_invocations()
    print("provider invocation regression checks passed")


if __name__ == "__main__":
    main()
