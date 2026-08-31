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


def test_capability_advertising_remains_evidence_gated() -> None:
    caps = CodexDriver().capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert caps.policy_capabilities == frozenset({"provider_state_protection"})
    assert "network_deny" not in caps.policy_capabilities
    assert "network_allowlist" not in caps.policy_capabilities


def test_helper_is_declarative_only() -> None:
    source = inspect.getsource(codex_task_egress_config_argv).lower()
    for forbidden in ("podman", "subprocess", "curl", "requests"):
        assert forbidden not in source


def main() -> None:
    test_pinned_deny_policy()
    test_pinned_allowlist_policy()
    test_invalid_egress_policy_fails_closed()
    test_capability_advertising_remains_evidence_gated()
    test_helper_is_declarative_only()
    print("Codex task egress deterministic regression checks passed")


if __name__ == "__main__":
    main()
