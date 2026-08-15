#!/usr/bin/env python3
"""Regression checks for the explicit executor network contract."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
AGENTD = Path(os.environ.get("AGENTD_UNDER_TEST", ROOT / "platform-src" / "bin" / "agentd"))
EXPECTED_NETWORK = "slirp4netns:allow_host_loopback=false"


def load_agentd():
    loader = importlib.machinery.SourceFileLoader("agentd_network_contract_test", str(AGENTD))
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


def network_values(args: list[str]) -> list[str]:
    result: list[str] = []
    for i, arg in enumerate(args):
        if arg == "--network" and i + 1 < len(args):
            result.append(args[i + 1])
        elif arg.startswith("--network="):
            result.append(arg.split("=", 1)[1])
    return result


def main() -> None:
    agentd = load_agentd()
    agentd.LOG.disabled = True

    assert agentd.PROVIDER_NETWORK_MODE == EXPECTED_NETWORK

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = make_cfg(root)

        for provider in ("codex", "cursor"):
            for readonly in (False, True):
                args = agentd.common_runtime_args(cfg, provider, readonly=readonly)
                assert network_values(args) == [EXPECTED_NETWORK], args
                assert "--http-proxy=false" in args
                assert "--network=host" not in args
                assert "--net=host" not in args

            offline = agentd.common_runtime_args(cfg, provider, network_enabled=False)
            assert network_values(offline) == ["none"], offline

        generic = agentd.common_runtime_args(cfg, None)
        assert network_values(generic) == ["none"], generic

        streamed: list[list[str]] = []
        subprocess_calls: list[list[str]] = []

        def fake_stream(_conn, argv, **_kwargs):
            streamed.append([str(x) for x in argv])
            return 0

        def fake_run(argv, **_kwargs):
            call = [str(x) for x in argv]
            subprocess_calls.append(call)
            if call[:3] == ["podman", "image", "exists"]:
                return SimpleNamespace(returncode=0)
            if "echo bad >> /workspace/marker" in call:
                return SimpleNamespace(returncode=1)
            if any("/dev/tcp/host.containers.internal/" in arg for arg in call):
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        agentd.seed_provider_home = lambda _cfg, _provider: None
        agentd.stream_noninteractive = fake_stream
        agentd.subprocess.run = fake_run
        agentd.send_output = lambda *_args, **_kwargs: None

        assert agentd.op_smoke(cfg, object()) == 0

        scoped_state_smokes = [
            call for call in streamed
            if any(".agent-dev-state-write-smoke" in arg for arg in call)
        ]
        assert len(scoped_state_smokes) == 2, scoped_state_smokes
        for call in scoped_state_smokes:
            assert network_values(call) == ["none"], call

        backend_smokes = [
            call for call in streamed
            if network_values(call) == [EXPECTED_NETWORK]
            and call[-3:] == ["bash", "-lc", "true"]
        ]
        assert len(backend_smokes) == 1, backend_smokes

        loopback_probes = [
            call for call in subprocess_calls
            if any("/dev/tcp/host.containers.internal/" in arg for arg in call)
        ]
        assert len(loopback_probes) == 1, loopback_probes
        assert network_values(loopback_probes[0]) == [EXPECTED_NETWORK]

    print("executor network contract regression checks passed")


if __name__ == "__main__":
    main()
