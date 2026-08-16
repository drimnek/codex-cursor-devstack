#!/usr/bin/env python3
"""Regression checks for scoped provider-state layout and legacy migration wiring."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
AGENTD = Path(os.environ.get("AGENTD_UNDER_TEST", ROOT / "platform-src" / "bin" / "agentd"))


def load_agentd():
    loader = importlib.machinery.SourceFileLoader("agentd_provider_state_test", str(AGENTD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load agentd")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def make_cfg(root: Path) -> dict:
    (root / "platform" / "seed" / "codex").mkdir(parents=True)
    (root / "platform" / "seed" / "cursor").mkdir(parents=True)
    (root / "platform" / "seed" / "codex" / "config.toml").write_text("[sandbox]\n")
    (root / "platform" / "seed" / "cursor" / "cli-config.json").write_text(
        '{"permissions":{"allow":["Read(**)"],"deny":[]}}\n'
    )
    return {
        "root": str(root),
        "limits": {"pids": 128, "memory": "2g", "cpus": "2"},
        "images": {
            "base": "localhost/agent-dev/base:test",
            "codex": "localhost/agent-dev/codex:test",
            "cursor": "localhost/agent-dev/cursor:test",
        },
    }


def mount_specs(args: list[str]) -> list[str]:
    result: list[str] = []
    for i, arg in enumerate(args):
        if arg in {"-v", "--volume"} and i + 1 < len(args):
            result.append(args[i + 1])
        elif arg.startswith("--volume="):
            result.append(arg.split("=", 1)[1])
    return result


def find_run(calls: list[list[str]], needle: str) -> list[str]:
    for call in calls:
        if call[:2] == ["podman", "run"] and needle in call:
            return call
    raise AssertionError(f"podman run containing {needle!r} not found: {calls}")


def main() -> None:
    agentd = load_agentd()
    agentd.LOG.disabled = True

    assert agentd.provider_volume("codex") == "agent-dev-codex-state"
    assert agentd.provider_volume("cursor") == "agent-dev-cursor-state"
    assert agentd.cursor_auth_volume() == "agent-dev-cursor-auth"
    assert agentd.legacy_provider_volume("codex") == "agent-dev-codex-home"
    assert agentd.legacy_provider_volume("cursor") == "agent-dev-cursor-home"
    assert agentd.provider_state_target("codex") == "/root/.codex"
    assert agentd.provider_state_target("cursor") == "/root/.cursor"
    assert agentd.cursor_auth_target() == "/root/.config/cursor"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = make_cfg(root)

        codex_args = agentd.common_runtime_args(cfg, "codex")
        cursor_args = agentd.common_runtime_args(cfg, "cursor")
        codex_mounts = mount_specs(codex_args)
        cursor_mounts = mount_specs(cursor_args)

        assert "agent-dev-codex-state:/root/.codex:rw" in codex_mounts
        assert "agent-dev-cursor-auth:/root/.config/cursor:rw" not in codex_mounts
        assert "agent-dev-cursor-auth:/root/.config/cursor:rw" in cursor_mounts
        assert "agent-dev-cursor-state:/root/.cursor:rw" in cursor_mounts
        assert not any(spec.split(":")[1] == "/root" for spec in codex_mounts)
        assert not any(spec.split(":")[1] == "/root" for spec in cursor_mounts)
        assert any(spec.endswith(":/root/.codex/config.toml:ro") for spec in codex_mounts)

        calls: list[list[str]] = []
        ensured: list[str] = []

        def fake_ensure_volume(name: str) -> None:
            ensured.append(name)

        def fake_run(argv, **kwargs):
            call = [str(x) for x in argv]
            calls.append(call)
            if call[:3] == ["podman", "volume", "exists"]:
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0)

        agentd.ensure_volume = fake_ensure_volume
        agentd.subprocess.run = fake_run

        agentd.seed_provider_home(cfg, "codex")
        assert ensured == ["agent-dev-codex-state"]
        migration = find_run(calls, "agent-dev-codex-state:/state:rw")
        assert "agent-dev-codex-home:/legacy:ro" in migration
        assert "--network=none" in migration
        assert "--http-proxy=false" in migration
        assert "--read-only" in migration
        assert "--cap-drop=all" in migration
        codex_script = migration[-1]
        assert "/legacy/.codex/." in codex_script
        assert "rm -f /state/config.toml" in codex_script
        assert ".agent-dev-state-layout-v2" in codex_script
        assert "non-empty but has no layout marker" in codex_script

        calls.clear()
        ensured.clear()
        agentd.seed_provider_home(cfg, "cursor")
        assert ensured == ["agent-dev-cursor-state", "agent-dev-cursor-auth"]
        migration = find_run(calls, "agent-dev-cursor-state:/state:rw")
        assert "agent-dev-cursor-auth:/auth:rw" in migration
        assert "agent-dev-cursor-home:/legacy:ro" in migration
        cursor_migration_script = migration[-1]
        assert "/legacy/.cursor/." in cursor_migration_script
        assert "/legacy/.config/cursor/." in cursor_migration_script
        assert ".agent-dev-state-layout-v2" in cursor_migration_script
        assert ".agent-dev-auth-layout-v1" in cursor_migration_script

        reconcile_calls = [
            call for call in calls
            if call[:2] == ["podman", "run"]
            and "agent-dev-cursor-state:/state:rw" in call
            and any(spec.endswith(":/seed/cli-config.json:ro") for spec in call)
        ]
        assert len(reconcile_calls) == 1, reconcile_calls
        reconcile_script = reconcile_calls[0][-1]
        assert "state=/state/cli-config.json" in reconcile_script
        assert "/state/.cursor/cli-config.json" not in reconcile_script
        assert ".permissions = $seed[0].permissions" in reconcile_script

        # If no legacy whole-home volume exists, the new scoped state is still
        # initialized directly and no implicit legacy volume is mounted.
        calls.clear()
        ensured.clear()

        def fake_run_no_legacy(argv, **kwargs):
            call = [str(x) for x in argv]
            calls.append(call)
            if call[:3] == ["podman", "volume", "exists"]:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        agentd.subprocess.run = fake_run_no_legacy
        agentd.seed_provider_home(cfg, "codex")
        migration = find_run(calls, "agent-dev-codex-state:/state:rw")
        assert "agent-dev-codex-home:/legacy:ro" not in migration
        assert ".agent-dev-state-layout-v2" in migration[-1]
        calls.clear()
        ensured.clear()
        agentd.seed_provider_home(cfg, "cursor")
        assert ensured == ["agent-dev-cursor-state", "agent-dev-cursor-auth"]
        migration = find_run(calls, "agent-dev-cursor-state:/state:rw")
        assert "agent-dev-cursor-auth:/auth:rw" in migration
        assert "agent-dev-cursor-home:/legacy:ro" not in migration
        assert ".agent-dev-state-layout-v2" in migration[-1]
        assert ".agent-dev-auth-layout-v1" in migration[-1]

        # Broker smoke must exercise both scoped provider-state mounts without
        # making model/API calls: direct /root writes fail, scoped state writes
        # succeed, and the smoke containers use network=none.
        smoke_streams: list[list[str]] = []

        def fake_stream(_conn, argv, **_kwargs):
            smoke_streams.append([str(x) for x in argv])
            return 0

        def fake_smoke_run(argv, **kwargs):
            call = [str(x) for x in argv]
            if call[:3] == ["podman", "image", "exists"]:
                return SimpleNamespace(returncode=0)
            if "echo bad >> /workspace/marker" in call:
                return SimpleNamespace(returncode=1)
            # HARD-03 expects the host-loopback probe to be rejected by the
            # explicit slirp4netns network boundary. Model that expected
            # connection failure rather than reporting a false security breach.
            if any("/dev/tcp/host.containers.internal/" in part for part in call):
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        agentd.seed_provider_home = lambda _cfg, _provider: None
        agentd.stream_noninteractive = fake_stream
        agentd.subprocess.run = fake_smoke_run
        agentd.send_output = lambda *_args, **_kwargs: None

        assert agentd.op_smoke(cfg, object()) == 0
        codex_smoke = next(
            call for call in smoke_streams
            if "agent-dev-codex-state:/root/.codex:rw" in call
        )
        cursor_smoke = next(
            call for call in smoke_streams
            if "agent-dev-cursor-state:/root/.cursor:rw" in call
        )
        assert "agent-dev-cursor-auth:/root/.config/cursor:rw" in cursor_smoke
        assert "/root/.config/cursor/.agent-dev-auth-write-smoke" in cursor_smoke[-1]
        for call, target in (
            (codex_smoke, "/root/.codex/.agent-dev-state-write-smoke"),
            (cursor_smoke, "/root/.cursor/.agent-dev-state-write-smoke"),
        ):
            assert "--network=none" in call
            assert "/root/.agent-dev-unexpected-write" in call[-1]
            assert target in call[-1]

    print("provider state layout regression checks passed")


if __name__ == "__main__":
    main()
