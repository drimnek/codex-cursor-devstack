#!/usr/bin/env python3
"""Deterministic MA2-SEC-007 Cursor task-egress configuration checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.cursor import (  # noqa: E402
    CURSOR_SANDBOX_POLICY_TARGET,
    CURSOR_SANDBOX_PRIVATE_DENIES,
    CursorDriver,
    cursor_task_egress_sandbox_json,
)


def parsed(**kwargs):
    return json.loads(cursor_task_egress_sandbox_json(**kwargs))


def test_review_and_implement_are_deny_by_default() -> None:
    review = parsed(workspace_access="read", mode="deny")
    implement = parsed(workspace_access="write", mode="deny")
    assert review["type"] == "workspace_readonly"
    assert implement["type"] == "workspace_readwrite"
    for document in (review, implement):
        assert document["networkPolicy"]["default"] == "deny"
        assert document["networkPolicy"]["allow"] == []
        assert tuple(document["networkPolicy"]["deny"]) == CURSOR_SANDBOX_PRIVATE_DENIES


def test_dependency_allowlist_is_exact_and_deterministic() -> None:
    document = parsed(
        workspace_access="write",
        mode="allowlist",
        destinations=("pypi.org", "registry.npmjs.org"),
    )
    assert document["networkPolicy"]["default"] == "deny"
    assert document["networkPolicy"]["allow"] == ["pypi.org", "registry.npmjs.org"]
    assert tuple(document["networkPolicy"]["deny"]) == CURSOR_SANDBOX_PRIVATE_DENIES


def test_invalid_native_policy_fails_closed() -> None:
    invalid = (
        {"workspace_access": "none", "mode": "deny"},
        {"workspace_access": "write", "mode": "allowlist"},
        {
            "workspace_access": "write",
            "mode": "deny",
            "destinations": ("unexpected.example.test",),
        },
        {"workspace_access": "write", "mode": "allow"},
    )
    for kwargs in invalid:
        try:
            cursor_task_egress_sandbox_json(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"invalid Cursor sandbox policy accepted: {kwargs!r}")


def test_capability_advertising_remains_evidence_gated() -> None:
    caps = CursorDriver().capabilities()
    assert caps.security_classes == frozenset({"compatibility"})
    assert "hardened" not in caps.security_classes
    assert caps.policy_capabilities == frozenset({"provider_state_protection"})
    assert "network_deny" not in caps.policy_capabilities
    assert "network_allowlist" not in caps.policy_capabilities
    assert CURSOR_SANDBOX_POLICY_TARGET == "/home/node/.cursor/sandbox.json"


def main() -> None:
    test_review_and_implement_are_deny_by_default()
    test_dependency_allowlist_is_exact_and_deterministic()
    test_invalid_native_policy_fails_closed()
    test_capability_advertising_remains_evidence_gated()
    print("Cursor task egress deterministic regression checks passed")


if __name__ == "__main__":
    main()
