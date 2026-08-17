#!/usr/bin/env python3
"""Regression coverage for GitNexus as a non-agent runtime consumer."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_agentd():
    path = ROOT / "platform-src/bin/agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_gitnexus_runtime", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.LOG.disabled = True
    return module


agentd = load_agentd()


@contextlib.contextmanager
def patched(module, **changes):
    original = {name: getattr(module, name) for name in changes}
    try:
        for name, value in changes.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(module, name, value)


@contextlib.contextmanager
def no_lock(_pp, _name, _readonly):
    yield


def config(root: Path) -> dict:
    return {
        "root": str(root),
        "state_dir": str(root / "state"),
        "images": {
            "base": "agent-dev-base:test",
            "codex": "agent-dev-codex:test",
            "cursor": "agent-dev-cursor:test",
            "intelligence": "agent-dev-intelligence:test",
        },
        "versions": {
            "platform": "test",
            "codex": "test",
            "gitnexus": "test",
        },
        "limits": {"pids": 64, "memory": "512m", "cpus": "1"},
    }


def project_layout(root: Path, *, mode: str = "integration"):
    project = root / "projects" / "demo"
    workspace = project / "worktrees" / "REQ-1"
    tasks = project / "tasks"
    reference = project / "reference"
    agent = project / "repo" / "agent"
    runtime = project / "runtime"
    for path in (workspace, tasks, reference, agent, runtime):
        path.mkdir(parents=True, exist_ok=True)
    meta = tasks / "REQ-1.json"
    meta.write_text("{}\n", encoding="utf-8")
    if mode == "parallel":
        (agent / ".git").mkdir()
    rec = {
        "mode": mode,
        "status": "active",
        "base_commit": "0123456789abcdef",
    }
    pp = {
        "root": project,
        "tasks": tasks,
        "reference": reference,
        "agent": agent,
        "runtime": runtime,
    }
    return rec, pp, workspace


def mount_specs(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-v"]


def check_index_uses_runtime_without_agent_driver() -> None:
    for mode in ("integration", "parallel"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = config(root)
            rec, pp, workspace = project_layout(root, mode=mode)
            calls: list[tuple[list[str], bool]] = []

            def execute(_cfg, _conn, fileobj, argv, *, interactive, **_kwargs):
                assert fileobj is None
                calls.append((list(argv), interactive))
                return 17

            def image_exists(argv, **_kwargs):
                assert argv == ["podman", "image", "exists", cfg["images"]["intelligence"]]
                return SimpleNamespace(returncode=0)

            def no_provider_resolution(*_args, **_kwargs):
                raise AssertionError("GitNexus indexing attempted AgentDriver resolution")

            with patched(
                agentd,
                load_task=lambda _cfg, _project, _task: (rec, pp, workspace),
                lock_one=no_lock,
                execute_runtime_argv=execute,
                registered_provider=no_provider_resolution,
                subprocess=SimpleNamespace(run=image_exists),
            ):
                rc = agentd.op_index(
                    cfg,
                    object(),
                    {"project": "demo", "task": "REQ-1"},
                )

            assert rc == 17
            assert len(calls) == 1
            argv, interactive = calls[0]
            assert interactive is False
            assert argv[:3] == ["podman", "run", "--rm"]
            assert "--network=none" in argv
            assert "--http-proxy=false" in argv
            assert "--read-only" in argv
            assert "--cap-drop=all" in argv
            assert "--security-opt=no-new-privileges" in argv
            assert "-e" in argv
            home_index = argv.index("-e")
            assert argv[home_index + 1] == "HOME=/gitnexus-home"
            assert argv[-6:] == [
                cfg["images"]["intelligence"],
                "gitnexus",
                "analyze",
                "--skip-agents-md",
                "--skip-skills",
                "--force",
            ]

            mounts = mount_specs(argv)
            gitnexus_home = pp["runtime"] / "gitnexus" / "REQ-1"
            assert f"{gitnexus_home}:/gitnexus-home:rw" in mounts
            assert f"{workspace}:/workspace:rw" in mounts
            assert f"{pp['reference']}:/reference:ro" in mounts
            assert f"{pp['tasks'] / 'REQ-1.json'}:/task/metadata.json:ro" in mounts
            if mode == "parallel":
                assert f"{workspace}:{workspace}:rw" in mounts
                assert f"{pp['agent'] / '.git'}:{pp['agent'] / '.git'}:rw" in mounts

            registry = gitnexus_home / ".gitnexus" / "registry.json"
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text("[]\n", encoding="utf-8")
            calls.clear()

            with patched(
                agentd,
                load_task=lambda _cfg, _project, _task: (rec, pp, workspace),
                lock_one=no_lock,
                execute_runtime_argv=execute,
                registered_provider=no_provider_resolution,
                subprocess=SimpleNamespace(run=image_exists),
            ):
                rc = agentd.op_index(
                    cfg,
                    object(),
                    {"project": "demo", "task": "REQ-1"},
                )

            assert rc == 17
            assert len(calls) == 1
            argv, interactive = calls[0]
            assert interactive is False
            assert "--force" not in argv
            assert f"{gitnexus_home}:/gitnexus-home:rw" in mount_specs(argv)
            assert "HOME=/gitnexus-home" in argv


def check_index_state_and_optional_image_behavior() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = config(root)
        rec, pp, workspace = project_layout(root)

        inactive = dict(rec, status="aborted")
        with patched(
            agentd,
            load_task=lambda _cfg, _project, _task: (inactive, pp, workspace),
        ):
            try:
                agentd.op_index(cfg, object(), {"project": "demo", "task": "REQ-1"})
            except agentd.RequestError as exc:
                assert "active/completed" in str(exc)
            else:
                raise AssertionError("inactive task indexing was accepted")

        with patched(
            agentd,
            load_task=lambda _cfg, _project, _task: (rec, pp, workspace),
            subprocess=SimpleNamespace(
                run=lambda *_args, **_kwargs: SimpleNamespace(returncode=1)
            ),
        ):
            try:
                agentd.op_index(cfg, object(), {"project": "demo", "task": "REQ-1"})
            except agentd.RequestError as exc:
                assert "optional GitNexus intelligence image is not available" in str(exc)
            else:
                raise AssertionError("missing optional intelligence image was accepted for indexing")


def check_optional_image_build_failure_is_nonfatal() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = config(root)
        output: list[bytes] = []
        build_calls: list[list[str]] = []

        def stream(_conn, argv, **_kwargs):
            build_calls.append(list(map(str, argv)))
            if "Containerfile.intelligence" in " ".join(map(str, argv)):
                return 23
            return 0

        with patched(
            agentd,
            stream_noninteractive=stream,
            seed_provider_home=lambda *_args, **_kwargs: None,
            write_build_lock=lambda _cfg: {"images": {}},
            send_output=lambda _conn, data: output.append(data),
        ):
            assert agentd.op_build(cfg, object()) == 0

        assert any("Containerfile.intelligence" in " ".join(argv) for argv in build_calls)
        assert any(
            b"optional GitNexus intelligence image failed to build" in item
            for item in output
        )
        assert any(b"Build lock written:" in item for item in output)


def check_non_agent_boundary() -> None:
    assert "gitnexus" not in agentd.AGENT_REGISTRY.ids()

    agents_dir = ROOT / "platform-src/agentdev/agents"
    agents_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in agents_dir.glob("*.py")
    ).lower()
    assert "gitnexus" not in agents_source

    runtime_source = (
        ROOT / "platform-src/agentdev/runtime/podman.py"
    ).read_text(encoding="utf-8").lower()
    assert "gitnexus" not in runtime_source

    daemon_source = (
        ROOT / "platform-src/agentdev/broker/daemon.py"
    ).read_text(encoding="utf-8")
    start = daemon_source.index("def op_index(")
    end = daemon_source.index("\ndef op_run(", start)
    index_source = daemon_source[start:end]
    assert "execute_runtime_argv(" in index_source
    assert "interactive=False" in index_source
    assert "HOME=/gitnexus-home" in index_source
    assert 'pp["runtime"] / "gitnexus" / task' in index_source
    assert 'argv.append("--force")' in index_source
    assert "stream_noninteractive(" not in index_source
    assert "registered_provider(" not in index_source
    assert "AGENT_REGISTRY" not in index_source


def main() -> None:
    check_index_uses_runtime_without_agent_driver()
    check_index_state_and_optional_image_behavior()
    check_optional_image_build_failure_is_nonfatal()
    check_non_agent_boundary()
    print("GitNexus non-agent runtime consumer regression checks passed")


if __name__ == "__main__":
    main()
