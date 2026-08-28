#!/usr/bin/env python3
"""Opt-in, model-free SEC-002 Codex direct-sandbox prerequisite probe.

Run as the deployed ``agentdev`` account after Codex authentication. The probe
never prints authentication material. It verifies that the control plane can
read the existing login state while a Codex-sandboxed task command cannot read
a sentinel mounted below the provider-state target or recover a secret-shaped
environment variable through normal inheritance, ancestor ``/proc`` state, or
ancestor descriptors.

This is a prerequisite probe, not SEC-002 certification. A passing result proves
that the required direct sandbox primitive is available on the deployed host;
SEC-002 still requires the authenticated provider T5/T6 acceptance record before
``provider_state_protection`` may be advertised.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

def _configure_import_path() -> None:
    """Support repository execution and a probe copied to a deployed temp path."""
    candidates: list[Path] = []

    configured = os.environ.get("AGENTDEV_PLATFORM_PYTHON")
    if configured:
        candidates.append(Path(configured))

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "platform-src"
        if (candidate / "agentdev").is_dir():
            candidates.append(candidate)
            break

    candidates.append(Path("/srv/agent-dev/platform"))
    for candidate in candidates:
        if (candidate / "agentdev").is_dir():
            sys.path.insert(0, str(candidate))
            return


_configure_import_path()

from agentdev.agents.codex import (
    CODEX_PROVIDER_STATE_TARGET,
    CODEX_RUNTIME_GID,
    CODEX_RUNTIME_UID,
    CodexDriver,
    codex_credential_confidentiality_config_argv,
)

RUN_ENV = "AGENTDEV_RUN_CODEX_CREDENTIAL_T6"
CONFIG_ENV = "AGENTDEV_CONFIG"
DEFAULT_CONFIG = Path("/srv/agent-dev/platform/config/platform.json")
PROBE_TARGET = f"{CODEX_PROVIDER_STATE_TARGET}/.agentdev-sec002-probe"


def run(argv: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=capture,
        check=False,
    )


def podman_base(cfg: dict, workspace: Path, secret_file: Path, secret_token: str) -> list[str]:
    root = Path(cfg["root"])
    seed = root / "platform" / "seed" / "codex" / "config.toml"
    if not seed.is_file():
        raise SystemExit(f"missing Codex seed config: {seed}")
    image = cfg["images"]["codex"]
    limits = cfg["limits"]
    state = CodexDriver().state_spec()[0]
    if state.target != CODEX_PROVIDER_STATE_TARGET:
        raise SystemExit(f"unexpected Codex state target: {state.target}")
    return [
        "podman", "run", "--rm", "--network=none", "--http-proxy=false",
        "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges",
        "--security-opt=unmask=/proc/*",
        "--user", f"{CODEX_RUNTIME_UID}:{CODEX_RUNTIME_GID}",
        f"--pids-limit={limits['pids']}", f"--memory={limits['memory']}",
        f"--cpus={limits['cpus']}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
        "-v", f"{state.source}:{state.target}:rw",
        "-v", f"{seed}:{CODEX_PROVIDER_STATE_TARGET}/config.toml:ro",
        "-v", f"{workspace}:/workspace:rw", "-w", "/workspace",
        "-v", f"{secret_file}:{PROBE_TARGET}:ro",
        "-e", f"AGENTDEV_SEC002_SECRET_TOKEN={secret_token}",
        "-e", "AGENT_TASK_ID=SEC002-PROBE",
        image,
    ]


def classify_sandbox_failure(stderr: str) -> bool:
    text = stderr.lower()
    return any(
        marker in text
        for marker in (
            "bwrap",
            "operation not permitted",
            "failed rtm_newaddr",
            "new namespace",
            "user namespace",
            "direct runtime enforcement",
        )
    )


def main() -> int:
    if os.environ.get(RUN_ENV) != "1":
        print(f"SKIP: set {RUN_ENV}=1 to run the deployed SEC-002 prerequisite probe")
        return 0

    config_path = Path(os.environ.get(CONFIG_ENV, str(DEFAULT_CONFIG)))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    image = cfg["images"]["codex"]
    if run(["podman", "image", "exists", image]).returncode != 0:
        raise SystemExit(f"missing Codex image: {image}")
    state = CodexDriver().state_spec()[0]
    if run(["podman", "volume", "exists", state.source]).returncode != 0:
        raise SystemExit(f"missing Codex state volume: {state.source}")

    with tempfile.TemporaryDirectory(prefix="agentdev-sec002-") as td:
        temp = Path(td)
        workspace = temp / "workspace"
        workspace.mkdir()
        secret_file = temp / "sentinel"
        sentinel = "agentdev-sec002-file-" + secrets.token_hex(16)
        secret_file.write_text(sentinel + "\n", encoding="utf-8")
        os.chmod(secret_file, 0o600)
        env_secret = "agentdev-sec002-env-" + secrets.token_hex(16)

        base = podman_base(cfg, workspace, secret_file, env_secret)

        # Control-plane proof: Codex can still consume its persistent login state.
        status = run([*base, "codex", "login", "status"], capture=True)
        if status.returncode != 0:
            sys.stderr.write(status.stderr)
            print("SEC002 CONTROL-PLANE FAIL: codex login status could not use provider state")
            return 3

        shell_probe = r'''set -eu
if cat /home/node/.codex/.agentdev-sec002-probe >/tmp/sec002-read 2>/dev/null; then
    echo provider-state-sentinel-readable
    exit 41
fi
if env | grep -F 'AGENTDEV_SEC002_SECRET_TOKEN=' >/dev/null; then
    echo secret-environment-inherited
    exit 42
fi
test "${AGENT_TASK_ID-}" = SEC002-PROBE || { echo controlled-task-env-missing; exit 43; }
pid=$PPID
steps=0
while [ "$pid" -gt 1 ] 2>/dev/null && [ "$steps" -lt 8 ]; do
    if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -F 'AGENTDEV_SEC002_SECRET_TOKEN=' >/dev/null; then
        echo secret-readable-from-ancestor-environ
        exit 44
    fi
    if [ -d "/proc/$pid/fd" ]; then
        for fd in /proc/$pid/fd/*; do
            target=$(readlink "$fd" 2>/dev/null || true)
            case "$target" in
                /home/node/.codex/*) echo provider-state-fd-visible; exit 45 ;;
            esac
        done
    fi
    next=$(awk '/^PPid:/ {print $2}' "/proc/$pid/status" 2>/dev/null || true)
    [ -n "$next" ] || break
    pid=$next
    steps=$((steps + 1))
done
echo SEC002_SANDBOX_PROBE_OK
'''
        config = list(codex_credential_confidentiality_config_argv("write"))
        # Equivalent CLI surface: codex sandbox linux -- bash -lc <probe>.
        probe = run(
            [*base, "codex", *config, "sandbox", "linux", "--", "bash", "-lc", shell_probe],
            capture=True,
        )
        combined = (probe.stdout or "") + (probe.stderr or "")
        if env_secret in combined or sentinel in combined:
            print("SEC002 SECURITY FAIL: secret sentinel leaked to probe output")
            return 5
        if probe.returncode != 0:
            sys.stderr.write(probe.stderr)
            if classify_sandbox_failure(probe.stderr):
                print(
                    "SEC002 PREREQUISITE NOT MET: Codex direct Linux sandbox cannot "
                    "enforce the credential-deny profile inside this executor"
                )
                return 2
            print(f"SEC002 SECURITY FAIL: task-shell probe exited {probe.returncode}")
            return probe.returncode or 6
        if "SEC002_SANDBOX_PROBE_OK" not in probe.stdout:
            print("SEC002 SECURITY FAIL: sandbox probe did not report success")
            return 7

    print("SEC002 PREREQUISITE PASS: direct Codex credential-deny sandbox is available")
    print("SEC002 remains uncertified until authenticated T5/T6 acceptance is recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
