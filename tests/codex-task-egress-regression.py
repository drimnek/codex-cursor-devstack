#!/usr/bin/env python3
"""Deterministic MA2-SEC-006 Codex task-egress configuration checks."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.codex import (  # noqa: E402
    CODEX_POLICY_COMPILER_BASELINE,
    CodexDriver,
    codex_task_egress_config_argv,
)


def test_pinned_deny_policy() -> None:
    assert CODEX_POLICY_COMPILER_BASELINE == "0.147.0"
    assert codex_task_egress_config_argv("deny") == (
        "-c",
        "sandbox_workspace_write.network_access=false",
    )


def test_pinned_allowlist_policy() -> None:
    argv = codex_task_egress_config_argv(
        "allowlist",
        ("allowed.example.test", "packages.example.test"),
    )
    for expected in (
        "sandbox_workspace_write.network_access=true",
        'projects={ "/workspace" = { trust_level = "untrusted" } }',
        "features.network_proxy.enabled=true",
        "features.network_proxy.enable_socks5=false",
        "features.network_proxy.enable_socks5_udp=false",
        "features.network_proxy.allow_upstream_proxy=false",
        "features.network_proxy.dangerously_allow_non_loopback_proxy=false",
        "features.network_proxy.dangerously_allow_all_unix_sockets=false",
        'features.network_proxy.mode="full"',
        "features.network_proxy.allow_local_binding=false",
        (
            'features.network_proxy.domains={ "allowed.example.test" = "allow", '
            '"packages.example.test" = "allow" }'
        ),
    ):
        assert expected in argv, expected
    assert argv.count(
        'projects={ "/workspace" = { trust_level = "untrusted" } }'
    ) == 1


def test_invalid_egress_policy_fails_closed() -> None:
    for mode, destinations in (
        ("deny", ("unexpected.example.test",)),
        ("allowlist", ()),
        ("allow", ()),
        ("", ()),
    ):
        try:
            codex_task_egress_config_argv(mode, destinations)
        except ValueError:
            continue
        raise AssertionError(
            f"invalid Codex egress policy accepted: {mode!r}, {destinations!r}"
        )


def test_sec006_network_capabilities_are_certified_while_hardened_remains_gated() -> None:
    caps = CodexDriver().capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert caps.policy_capabilities == frozenset(
        {"provider_state_protection", "network_deny", "network_allowlist"}
    )


def test_helper_is_declarative_only() -> None:
    source = inspect.getsource(codex_task_egress_config_argv).lower()
    for forbidden in ("podman", "subprocess", "curl", "requests"):
        assert forbidden not in source


def test_deployed_probe_consumes_common_contract() -> None:
    source = (ROOT / "tests/e2e/codex-task-egress-probe.py").read_text(
        encoding="utf-8"
    )
    required = (
        "from contracts.task_egress import",
        "evaluate_task_egress_contract",
        "task_egress_contract",
        "PROBE_ALLOWED_DESTINATION",
        "PROBE_DENIED_PUBLIC",
        "PROBE_NON_ALLOWLISTED_DESTINATION",
        "PROBE_LOOPBACK_IPV4",
        "PROBE_PRIVATE_IPV4",
        "PROBE_METADATA_IPV4",
        "PROBE_RAW_IP_BYPASS",
        "PROBE_IPV6_PUBLIC",
        "PROBE_IPV6_LOOPBACK",
        "PROBE_IPV6_PRIVATE",
        "PROBE_IPV6_LINK_LOCAL",
        "PROBE_IPV6_RAW_IP_BYPASS",
        "PROBE_REDIRECT_BYPASS",
        "PROBE_PROVIDER_CONTROL_CONNECTIVITY",
        "AGENTDEV_RUN_CODEX_EGRESS_T6",
        "AGENTDEV_CODEX_EGRESS_ALLOWED_URL",
        "AGENTDEV_CODEX_EGRESS_DENIED_URL",
        "AGENTDEV_CODEX_EGRESS_REDIRECT_URL",
        "AGENTDEV_CODEX_EGRESS_RAW_IP_URL",
        "AGENTDEV_CODEX_EGRESS_IPV6_UNSUPPORTED_REASON",
        "SEC006 T6 PROJECT-CONFIG WIDENING ATTEMPT INSTALLED",
        "SEC006 AUTHENTICATED T5/T6 EGRESS PROOF PASS",
        "codex_task_egress_config_argv",
        "codex_credential_confidentiality_config_argv",
        "--noproxy",
        "127.0.0.1",
        "169.254.169.254",
        "[::1]",
        "[fd00::1]",
        "[fe80::1]",
        "--skip-git-repo-check",
        "--directory /tmp",
        "PRIVATE_IP_HANDOFF",
        "chmod 0444",
        "hostname -I",
        "os.chmod(workspace, 0o777)",
        "SEC006 local-control loopback HTTP reachability failed",
        "SEC006 local-control private IPv4 HTTP reachability failed",
        "COMPACT_OBSERVATION_PREFIX",
        "COMPACT_PROBE_KEYS",
        "SEC006_OBS:",
        'SEC006_VECTOR="${SEC006_VECTOR}${key}${state};"',
        "compact task observation",
        "2>/dev/null || code=$?",
        "DEPENDENCY_PROBE_START_PREFIX",
        "dependency_probe_scripts",
        "run_dependency_probe",
        "run_dependency_profile",
        'task_egress_contract("dependency")',
        "expectation.probe_id",
        "exec_command failed for",
        "exited -1",
        "Codex managed-network execution cancelled after probe start",
        "CODEX_NETWORK_POLICY_DENIAL_MARKERS",
        "codex_network_policy_denied",
        "explicit_policy_denial",
        "Codex explicit network-policy denial",
        "network access was blocked by policy.",
        "domain is not on the allowlist for the current sandbox mode",
        "domain not in allowlist.",
        "request blocked by network policy.",
        "blocked-by-allowlist",
        "[truncated]",
        "SEC006 network capabilities certified; hardened remains evidence-gated",
    )
    for marker in required:
        assert marker in source, marker

    assert source.count("hostname -I") == 1
    assert source.count("cat {shlex.quote(PRIVATE_IP_HANDOFF)}") == 2
    assert source.count("SEC006_OBS:") == 1
    assert source.count("DEPENDENCY_PROBE_START_PREFIX") >= 2
    assert "explicit_policy_denial or cancelled_after_start" in source

    lower = source.lower()
    for forbidden in (
        "google.com",
        "github.com",
        "pypi.org",
        "npmjs.org",
        "openai.com",
        "--cap-add",
        "danger-full-access",
        "/workspace/.agentdev-sec006-private-ip",
        "/tmp/.agentdev-sec006-egress-observations",
        ">> \"$SEC006_OBSERVATIONS\"",
        "SEC006_OBSERVATIONS_BEGIN",
        "SEC006_OBSERVATIONS_END",
        "printf '%s\\t%s\\t%s\\n'",
        "def dependency_profile_script(",
    ):
        assert forbidden not in lower, forbidden


def main() -> None:
    test_pinned_deny_policy()
    test_pinned_allowlist_policy()
    test_invalid_egress_policy_fails_closed()
    test_sec006_network_capabilities_are_certified_while_hardened_remains_gated()
    test_helper_is_declarative_only()
    test_deployed_probe_consumes_common_contract()
    print("Codex task egress deterministic regression checks passed")


if __name__ == "__main__":
    main()
