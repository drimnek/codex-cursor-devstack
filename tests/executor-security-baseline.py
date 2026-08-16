#!/usr/bin/env python3
"""Freeze the pre-v0.2 executor security baseline and prove regressions fail."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTD = ROOT / "platform-src/bin/agentd"
AUDIT = ROOT / "tests/executor-boundary-source-audit.py"


class BaselineError(RuntimeError):
    """Expected security-baseline validation failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BaselineError(message)


def run_audit(agentd: Path) -> tuple[int, dict]:
    env = os.environ.copy()
    env["AGENTD_UNDER_TEST"] = str(agentd)
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--json"],
        env=env,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no audit output"
        raise BaselineError(f"executor boundary audit did not emit valid JSON: {detail}") from exc
    return proc.returncode, payload


def findings_for(payload: dict, fragment: str) -> list[dict]:
    return [
        finding
        for finding in payload.get("findings", [])
        if fragment in str(finding.get("check", ""))
    ]


def require_finding_level(payload: dict, fragment: str, level: str) -> None:
    matches = findings_for(payload, fragment)
    require(matches, f"security audit has no finding matching {fragment!r}")
    bad = [finding for finding in matches if finding.get("level") != level]
    require(
        not bad,
        f"security audit findings matching {fragment!r} are not all {level}: {bad}",
    )


def mount_target(spec: str) -> str:
    parts = spec.split(":")
    return parts[1] if len(parts) >= 2 else ""


def check_current_baseline(payload: dict) -> None:
    summary = payload.get("summary", {})
    require(summary.get("FAIL") == 0, f"closed executor security guarantees regressed: {summary}")

    closed_fragments = (
        "rootfs read-only",
        "capabilities dropped",
        "no-new-privileges",
        "workspace mode",
        "reference mode",
        "task metadata mode",
        "whole provider home exposure",
        "provider state mount",
        "provider network contract",
        "host loopback isolation",
        "forbidden mount targets",
        "host namespace/privileged flags",
        "device passthrough",
        "proxy env inheritance",
        "Non-provider executor network",
        "Explicit host secret env forwarding",
        "Explicit task environment",
    )
    for fragment in closed_fragments:
        require_finding_level(payload, fragment, "PASS")

    # These are intentionally OPEN in the v0.1 baseline. Their continued WARN
    # classification prevents them from being mistaken for hardened guarantees.
    open_warn_fragments = (
        "provider credentials readable",
        "outbound egress",
    )
    for fragment in open_warn_fragments:
        require_finding_level(payload, fragment, "WARN")

    warnings = [
        finding
        for finding in payload.get("findings", [])
        if finding.get("level") == "WARN"
    ]
    unexpected_warnings = [
        finding
        for finding in warnings
        if not any(fragment in str(finding.get("check", "")) for fragment in open_warn_fragments)
    ]
    require(
        not unexpected_warnings,
        f"security audit contains unclassified WARN findings: {unexpected_warnings}",
    )

    # Cursor authentication persistence intentionally adds another executor-
    # readable credential-bearing mount. Freeze that fact explicitly even
    # though the existing source audit reports credentials at provider-state level.
    inventory = payload.get("inventory", [])
    cursor_rows = [row for row in inventory if row.get("provider") == "cursor"]
    codex_rows = [row for row in inventory if row.get("provider") == "codex"]
    require(cursor_rows and codex_rows, "security audit inventory must contain Codex and Cursor rows")

    cursor_auth_spec = "agent-dev-cursor-auth:/root/.config/cursor:rw"
    for row in cursor_rows:
        mounts = row.get("mounts", [])
        require(
            cursor_auth_spec in mounts,
            f"Cursor executor is missing scoped persistent auth mount: {mounts}",
        )
    for row in codex_rows:
        mounts = row.get("mounts", [])
        require(
            all(mount_target(spec) != "/root/.config/cursor" for spec in mounts),
            f"Codex executor unexpectedly receives Cursor authentication state: {mounts}",
        )


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    require(count == 1, f"cannot build {label} mutation: expected one source match, got {count}")
    return source.replace(old, new, 1)


def replace_in_common_runtime(source: str, old: str, new: str, label: str) -> str:
    start = source.find("def common_runtime_args(")
    end = source.find("\ndef stream_noninteractive(", start)
    require(start >= 0 and end > start, "cannot locate common_runtime_args source boundary")
    body = source[start:end]
    mutated = replace_once(body, old, new, label)
    return source[:start] + mutated + source[end:]


def make_mutations(source: str) -> list[tuple[str, str, str]]:
    no_read_only = replace_in_common_runtime(
        source,
        '"--http-proxy=false", "--read-only", "--cap-drop=all"',
        '"--http-proxy=false", "--cap-drop=all"',
        "read-only rootfs",
    )
    loopback_enabled = replace_once(
        source,
        'PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=false"',
        'PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=true"',
        "provider network isolation",
    )
    socket_exposed = replace_in_common_runtime(
        source,
        "    if provider is not None:\n",
        '    args += ["-v", "/run/podman/podman.sock:/run/podman/podman.sock:rw"]\n'
        "    if provider is not None:\n",
        "forbidden socket mount",
    )
    return [
        ("rootfs-read-only", no_read_only, "rootfs read-only"),
        ("provider-host-loopback", loopback_enabled, "provider network contract"),
        ("podman-socket-exposure", socket_exposed, "forbidden mount targets"),
    ]


def check_mutation_failures(source: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, mutant, expected_check in make_mutations(source):
            path = tmp / f"agentd-{name}"
            path.write_text(mutant, encoding="utf-8")
            rc, payload = run_audit(path)
            require(rc == 1, f"{name} mutation did not make the security audit fail (exit={rc})")
            require(payload.get("summary", {}).get("FAIL", 0) > 0, f"{name} mutation produced no FAIL findings")
            require_finding_level(payload, expected_check, "FAIL")


def main() -> int:
    try:
        require(AGENTD.is_file(), f"agentd source not found: {AGENTD}")
        require(AUDIT.is_file(), f"executor boundary audit not found: {AUDIT}")
        rc, payload = run_audit(AGENTD)
        require(rc == 0, f"current executor boundary audit failed with exit code {rc}")
        check_current_baseline(payload)
        check_mutation_failures(AGENTD.read_text(encoding="utf-8"))
    except BaselineError as exc:
        print(f"executor security baseline failed: {exc}", file=sys.stderr)
        return 1

    print("executor security baseline checks passed")
    print("accepted open findings: provider credential readability; destination-level outbound egress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
