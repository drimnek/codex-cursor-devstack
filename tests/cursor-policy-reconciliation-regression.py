#!/usr/bin/env python3
"""Regression checks for Cursor runtime policy reconciliation."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def load_agentd(path: Path):
    loader = importlib.machinery.SourceFileLoader("agentd_cursor_policy_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def capture_cursor_seed_script(module, root: Path) -> str:
    seed_dir = root / "platform" / "seed" / "cursor"
    seed_dir.mkdir(parents=True)
    (seed_dir / "cli-config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "permissions": {
                    "allow": ["Read(**)", "Write(**)", "Shell(git)"],
                    "deny": ["Shell(sudo)"],
                },
            }
        )
        + "\n"
    )

    captured: dict[str, list[str]] = {}
    original_run = module.subprocess.run
    original_ensure_volume = module.ensure_volume

    def fake_run(argv, *args, **kwargs):
        captured["argv"] = [str(item) for item in argv]
        return subprocess.CompletedProcess(argv, 0)

    try:
        module.ensure_volume = lambda _name: None
        module.subprocess.run = fake_run
        module.seed_provider_home(
            {
                "root": str(root),
                "images": {"base": "test-base"},
            },
            "cursor",
        )
    finally:
        module.subprocess.run = original_run
        module.ensure_volume = original_ensure_volume

    argv = captured["argv"]
    assert "/seed/cli-config.json:ro" in " ".join(argv)
    return argv[-1]


def run_script(script: str, state_root: Path, seed_file: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    transformed = script.replace("/state", str(state_root)).replace(
        "/seed/cli-config.json", str(seed_file)
    )
    subprocess.run(["bash", "-lc", transformed], check=True)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    module = load_agentd(repo_root / "platform-src" / "bin" / "agentd")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "platform-root"
        script = capture_cursor_seed_script(module, root)
        seed_file = root / "platform" / "seed" / "cursor" / "cli-config.json"
        seed = json.loads(seed_file.read_text())

        # Missing active config: materialize the complete seed.
        first_state = Path(td) / "first-state"
        run_script(script, first_state, seed_file)
        first_config = first_state / "cli-config.json"
        assert json.loads(first_config.read_text()) == seed
        assert stat.S_IMODE(first_config.stat().st_mode) == 0o600

        # Existing active config: replace only platform-managed permissions.
        stale_state = Path(td) / "stale-state"
        stale_config = stale_state / "cli-config.json"
        stale_config.parent.mkdir(parents=True)
        stale = {
            "version": 1,
            "model": "cursor-managed-model",
            "customCursorField": {"preserve": True},
            "permissions": {"allow": ["Shell(ls)"], "deny": []},
        }
        stale_config.write_text(json.dumps(stale) + "\n")
        os.chmod(stale_config, 0o600)

        run_script(script, stale_state, seed_file)
        reconciled = json.loads(stale_config.read_text())
        assert reconciled["permissions"] == seed["permissions"]
        assert reconciled["model"] == stale["model"]
        assert reconciled["customCursorField"] == stale["customCursorField"]
        assert stat.S_IMODE(stale_config.stat().st_mode) == 0o600
        assert not list(stale_config.parent.glob("cli-config.json.tmp.*"))

    print("Cursor policy reconciliation regression checks passed")


if __name__ == "__main__":
    main()
