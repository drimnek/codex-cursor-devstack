#!/usr/bin/env python3
"""Audit the executor boundary encoded by platform-src/bin/agentd.

This is intentionally source-level and does not invoke Podman or a provider.
It reports PASS/WARN/FAIL so hardening gaps are visible before changing runtime.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import inspect
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
AGENTD = Path(os.environ.get("AGENTD_UNDER_TEST", ROOT / "platform-src" / "bin" / "agentd"))


@dataclass
class Finding:
    level: str
    check: str
    detail: str


class Audit:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, level: str, check: str, detail: str) -> None:
        self.findings.append(Finding(level, check, detail))

    def require(self, ok: bool, check: str, detail_ok: str, detail_fail: str) -> None:
        self.add("PASS" if ok else "FAIL", check, detail_ok if ok else detail_fail)

    def warn(self, condition: bool, check: str, detail: str) -> None:
        if condition:
            self.add("WARN", check, detail)
        else:
            self.add("PASS", check, "no exposure detected")


def load_agentd():
    if not AGENTD.is_file():
        raise SystemExit(f"agentd source not found: {AGENTD}")
    loader = importlib.machinery.SourceFileLoader("agentd_boundary_audit", str(AGENTD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load agentd")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def value_after(args: list[str], option: str) -> str | None:
    try:
        i = args.index(option)
    except ValueError:
        return None
    if i + 1 >= len(args):
        return None
    return args[i + 1]


def prefixed_value(args: list[str], prefix: str) -> str | None:
    for arg in args:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def mounts(args: list[str]) -> list[str]:
    result: list[str] = []
    for i, arg in enumerate(args):
        if arg in {"-v", "--volume"} and i + 1 < len(args):
            result.append(args[i + 1])
        elif arg.startswith("--volume="):
            result.append(arg.split("=", 1)[1])
    return result


def mount_target(spec: str) -> str:
    parts = spec.split(":")
    return parts[1] if len(parts) >= 2 else ""


def find_mount(args: list[str], target: str) -> str | None:
    for spec in mounts(args):
        if mount_target(spec) == target:
            return spec
    return None


def mount_mode(spec: str | None) -> str:
    if not spec:
        return ""
    parts = spec.split(":")
    return parts[-1] if len(parts) >= 3 else ""


def has_tmpfs(args: list[str], target: str, required: Iterable[str]) -> bool:
    vals: list[str] = []
    for i, arg in enumerate(args):
        if arg == "--tmpfs" and i + 1 < len(args):
            vals.append(args[i + 1])
        elif arg.startswith("--tmpfs="):
            vals.append(arg.split("=", 1)[1])
    for val in vals:
        head, _, opts = val.partition(":")
        if head == target and all(opt in opts.split(",") for opt in required):
            return True
    return False


def network_mode(args: list[str]) -> str:
    for i, arg in enumerate(args):
        if arg == "--network" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--network="):
            return arg.split("=", 1)[1]
    return "default"


def make_fixture(tmp: Path) -> tuple[dict, Path, Path, Path, Path]:
    root = tmp / "srv-agent-dev"
    (root / "platform" / "seed" / "codex").mkdir(parents=True)
    (root / "platform" / "seed" / "cursor").mkdir(parents=True)
    (root / "platform" / "seed" / "codex" / "config.toml").write_text("sandbox_mode = 'workspace-write'\n")
    (root / "platform" / "seed" / "cursor" / "cli-config.json").write_text('{"permissions": {}}\n')

    project = root / "projects" / "audit"
    workspace = project / "worktrees" / "REQ-AUDIT"
    reference = project / "reference"
    tasks = project / "tasks"
    git_common = project / "repo" / "agent" / ".git"
    for path in (workspace, reference, tasks, git_common):
        path.mkdir(parents=True, exist_ok=True)
    task_meta = tasks / "REQ-AUDIT.json"
    task_meta.write_text('{"task": "REQ-AUDIT"}\n')

    cfg = {
        "root": str(root),
        "limits": {"pids": 1024, "memory": "8g", "cpus": 4},
        "images": {
            "base": "localhost/agent-dev/base:0.1.0",
            "codex": "localhost/agent-dev/codex:0.1.0",
            "cursor": "localhost/agent-dev/cursor:0.1.0",
        },
    }
    return cfg, workspace, reference, task_meta, git_common


def audit_args(audit: Audit, provider: str, readonly: bool, args: list[str], workspace: Path) -> dict:
    label = f"{provider} {'RO' if readonly else 'RW'}"
    audit.require("--read-only" in args, f"{label}: rootfs read-only", "--read-only present", "--read-only missing")
    audit.require(
        "--cap-drop=all" in args or ("--cap-drop" in args and value_after(args, "--cap-drop") == "all"),
        f"{label}: capabilities dropped", "all capabilities dropped", "cap-drop=all missing",
    )
    audit.require(
        "--security-opt=no-new-privileges" in args or value_after(args, "--security-opt") == "no-new-privileges",
        f"{label}: no-new-privileges", "no-new-privileges present", "no-new-privileges missing",
    )
    audit.require(prefixed_value(args, "--pids-limit=") is not None, f"{label}: PID limit", "PID limit present", "PID limit missing")
    audit.require(prefixed_value(args, "--memory=") is not None, f"{label}: memory limit", "memory limit present", "memory limit missing")
    audit.require(prefixed_value(args, "--cpus=") is not None, f"{label}: CPU limit", "CPU limit present", "CPU limit missing")
    audit.require(has_tmpfs(args, "/tmp", {"rw", "nosuid", "nodev"}), f"{label}: /tmp tmpfs", "rw,nosuid,nodev", "secure /tmp tmpfs missing")
    audit.require(has_tmpfs(args, "/run", {"rw", "nosuid", "nodev"}), f"{label}: /run tmpfs", "rw,nosuid,nodev", "secure /run tmpfs missing")

    ws = find_mount(args, "/workspace")
    expected = "ro" if readonly else "rw"
    audit.require(ws is not None and expected in mount_mode(ws).split(","), f"{label}: workspace mode", f"workspace mounted {expected}", f"workspace is not mounted {expected}: {ws}")

    ref = find_mount(args, "/reference")
    audit.require(ref is not None and "ro" in mount_mode(ref).split(","), f"{label}: reference mode", "reference mounted ro", f"reference is not ro: {ref}")
    meta = find_mount(args, "/task/metadata.json")
    audit.require(meta is not None and "ro" in mount_mode(meta).split(","), f"{label}: task metadata mode", "task metadata mounted ro", f"task metadata is not ro: {meta}")

    expected_state_target = f"/root/{'.codex' if provider == 'codex' else '.cursor'}"
    root_home = find_mount(args, "/root")
    state_mount = find_mount(args, expected_state_target)
    audit.require(root_home is None, f"{label}: whole provider home exposure", "no persistent volume mounted at /root", f"unexpected whole-home mount: {root_home}")
    audit.require(state_mount is not None, f"{label}: provider state mount", f"provider state mounted only at {expected_state_target}", f"provider state mount missing at {expected_state_target}")
    if state_mount:
        source = state_mount.split(":", 1)[0]
        audit.require(not source.startswith("/"), f"{label}: provider state source", "named volume, not host path", f"host path mounted as provider state: {source}")
        audit.require("rw" in mount_mode(state_mount).split(","), f"{label}: provider state writable", "scoped provider state is writable", f"provider state is not writable: {state_mount}")
        audit.add("WARN", f"{label}: provider credentials readable", f"provider auth/config/cache under {expected_state_target} remain readable by executor processes; credential confidentiality is not solved by HARD-02")

    net = network_mode(args)
    if net == "none":
        audit.add("WARN", f"{label}: network", "network=none; provider API calls would normally require outbound connectivity")
    else:
        audit.add("WARN", f"{label}: network", f"network mode is {net!r}; outbound connectivity is available and should be treated as part of the provider trust boundary")

    forbidden_targets = {"/var/run/docker.sock", "/run/podman/podman.sock", "/home", "/host", "/srv/agent-dev"}
    exposed = sorted(spec for spec in mounts(args) if mount_target(spec) in forbidden_targets)
    audit.require(not exposed, f"{label}: forbidden mount targets", "no forbidden host/socket targets", f"forbidden mounts: {exposed}")

    dangerous_flags = {"--privileged", "--env-host", "--pid=host", "--ipc=host", "--uts=host", "--userns=host", "--network=host", "--net=host"}
    present_dangerous = sorted(flag for flag in dangerous_flags if flag in args)
    device_flags = [arg for arg in args if arg == "--device" or arg.startswith("--device=")]
    audit.require(not present_dangerous, f"{label}: host namespace/privileged flags", "no privileged or host-namespace flags", f"dangerous flags: {present_dangerous}")
    audit.require(not device_flags, f"{label}: device passthrough", "no explicit device passthrough", f"device flags: {device_flags}")

    proxy_disabled = "--http-proxy=false" in args or (value_after(args, "--http-proxy") == "false")
    audit.require(
        proxy_disabled,
        f"{label}: proxy env inheritance",
        "Podman HTTP proxy propagation explicitly disabled",
        "Podman HTTP proxy propagation must be explicitly disabled with --http-proxy=false",
    )

    return {
        "provider": provider,
        "mode": "readonly" if readonly else "write",
        "network": net,
        "workspace_mount": ws,
        "provider_state_mount": state_mount,
        "reference_mount": ref,
        "task_metadata_mount": meta,
        "mounts": mounts(args),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable inventory")
    parser.add_argument("--fail-on-warn", action="store_true", help="return non-zero if any WARN is present")
    ns = parser.parse_args()

    agentd = load_agentd()
    audit = Audit()
    inventory: list[dict] = []

    with tempfile.TemporaryDirectory() as td:
        cfg, workspace, reference, task_meta, git_common = make_fixture(Path(td))
        for provider in ("codex", "cursor"):
            for readonly in (False, True):
                args = agentd.common_runtime_args(
                    cfg,
                    provider,
                    workspace,
                    readonly=readonly,
                    reference=reference,
                    task_meta=task_meta,
                    git_common=git_common,
                )
                inventory.append(audit_args(audit, provider, readonly, args, workspace))

        audit.require(agentd.provider_volume("codex") == "agent-dev-codex-state", "Codex scoped state volume", "uses agent-dev-codex-state", f"unexpected volume: {agentd.provider_volume('codex')}")
        audit.require(agentd.provider_volume("cursor") == "agent-dev-cursor-state", "Cursor scoped state volume", "uses agent-dev-cursor-state", f"unexpected volume: {agentd.provider_volume('cursor')}")
        audit.require(agentd.legacy_provider_volume("codex") == "agent-dev-codex-home", "Codex legacy state source", "legacy whole-home volume retained only as migration source", f"unexpected legacy volume: {agentd.legacy_provider_volume('codex')}")
        audit.require(agentd.legacy_provider_volume("cursor") == "agent-dev-cursor-home", "Cursor legacy state source", "legacy whole-home volume retained only as migration source", f"unexpected legacy volume: {agentd.legacy_provider_volume('cursor')}")

        codex_args = agentd.common_runtime_args(cfg, "codex", workspace, readonly=False, reference=reference, task_meta=task_meta)
        codex_policy = find_mount(codex_args, "/root/.codex/config.toml")
        audit.require(codex_policy is not None and "ro" in mount_mode(codex_policy).split(","), "Codex policy mount", "Codex config policy mounted ro", f"Codex policy mount not ro: {codex_policy}")

        cursor_args = agentd.common_runtime_args(cfg, "cursor", workspace, readonly=False, reference=reference, task_meta=task_meta)
        cursor_policy = find_mount(cursor_args, "/root/.cursor/cli-config.json")
        audit.require(cursor_policy is None, "Cursor active config", "no immutable bind overlays writable Cursor active config", f"unexpected direct Cursor config bind: {cursor_policy}")

        op_run_source = inspect.getsource(agentd.op_run)
        secret_markers = ("SSH_AUTH_SOCK", "GPG_AGENT_INFO", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
        present_secrets = [name for name in secret_markers if name in op_run_source]
        audit.require(not present_secrets, "Explicit host secret env forwarding", "no known host secret variables are explicitly forwarded by op_run", f"explicit secret env markers found: {present_secrets}")
        agent_vars = sorted(set(re.findall(r"AGENT_[A-Z0-9_]+", op_run_source)))
        expected_agent_vars = ["AGENT_TASK_BASE_COMMIT", "AGENT_TASK_ID", "AGENT_TASK_MODE"]
        audit.require(agent_vars == expected_agent_vars, "Explicit task environment", f"only expected task metadata variables forwarded: {', '.join(agent_vars)}", f"unexpected AGENT_* environment set: {agent_vars}")

    counts = {level: sum(f.level == level for f in audit.findings) for level in ("PASS", "WARN", "FAIL")}
    if ns.json:
        print(json.dumps({"agentd": str(AGENTD), "summary": counts, "inventory": inventory, "findings": [f.__dict__ for f in audit.findings]}, indent=2, sort_keys=True))
    else:
        print(f"executor boundary source audit: {AGENTD}")
        for finding in audit.findings:
            print(f"{finding.level:4} {finding.check}: {finding.detail}")
        print(f"SUMMARY PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")

    if counts["FAIL"]:
        return 1
    if ns.fail_on_warn and counts["WARN"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
