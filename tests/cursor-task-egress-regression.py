#!/usr/bin/env python3
"""Deterministic MA2-SEC-007 Cursor task-egress configuration checks."""
from __future__ import annotations

import importlib.util
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


def test_pinned_cli_network_mode_reconciliation() -> None:
    seed = json.loads(
        (ROOT / "platform-src/seed/cursor/cli-config.json").read_text(encoding="utf-8")
    )
    assert seed["sandbox"] == {"networkAccess": "user_config_only"}

    reconciliation = CursorDriver().state_adapter().reconciliation
    assert reconciliation is not None
    assert reconciliation.managed_field == "permissions"
    assert reconciliation.managed_paths == (("sandbox", "networkAccess"),)


def test_generated_task_probe_observation_handoff_is_self_contained() -> None:
    probe_path = ROOT / "tests/e2e/cursor-task-egress-probe.py"
    spec = importlib.util.spec_from_file_location(
        "cursor_task_egress_e2e_regression",
        probe_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.task_probe_source(
        denied_url="https://denied.example.test/",
        allowed_url="https://allowed.example.test/",
        raw_ipv4_url="http://203.0.113.10/",
        redirect_url="https://allowed.example.test/redirect",
        defaults_url="https://defaults.example.test/",
        ipv6_public_url=None,
        ipv6_raw_url=None,
    )
    compile(generated, "<cursor-sec007-task-probe>", "exec")
    assert "sandbox_line" not in generated
    assert module.TASK_OBSERVATION_HANDOFF in generated
    assert "DEFAULTS_PREFIX" in generated
    assert "OBS_PREFIX" in generated


def test_deployed_probe_consumes_common_contract_and_cursor_bypass_checks() -> None:
    source = (ROOT / "tests/e2e/cursor-task-egress-probe.py").read_text(
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
        "AGENTDEV_RUN_CURSOR_EGRESS_T6",
        "AGENTDEV_CURSOR_EGRESS_DEFAULTS_URL",
        "CURSOR_SANDBOX_POLICY_TARGET",
        "cursor_task_egress_sandbox_json",
        "SEC007 T5 CLI SANDBOX CONFIG",
        "SEC007 T5 CLI SANDBOX NETWORK MODE PASS",
        "SEC007 T5 AUTHENTICATED CONTROL PASS",
        "user_config_only",
        "TASK_OBSERVATION_HANDOFF",
        "SEC007_TASK_OBSERVATION_HANDOFF_MISSING",
        "parse_observations(task_observation)",
        "SEC007 T6 PROJECT-SANDBOX WIDENING ATTEMPT INSTALLED",
        "SEC007 T6 BASELINE POLICY PHASE",
        "SEC007 T6 PROJECT-WIDENING POLICY PHASE",
        "SEC007 T6 PROJECT-SANDBOX WIDENING DENIED",
        'phase="baseline"',
        'phase="project-widening"',
        "CURSOR DEFAULT-DOMAIN BYPASS DENIED",
        "baseline policy leaked a Cursor-default ",
        "destination despite user_config_only; inspect effective global policy ",
        "baseline passed but repository sandbox ",
        "policy widened the effective Cursor policy; prepare a broker-owned ",
        "169.254.169.254",
        "127.0.0.1",
        "[::1]",
        "[fd00::1]",
        "[fe80::1]",
        "hostname -I",
        "chmod 0444",
        "python3 /workspace/",
        "--noproxy",
        "--sandbox",
        "enabled",
        "SEC007 AUTHENTICATED T5/T6 EGRESS PROOF PASS",
        "network capabilities remain evidence-gated pending certification",
    )
    for marker in required:
        assert marker in source, marker

    lower = source.lower()
    for forbidden in (
        "danger-full-access",
        "--cap-add",
        "T5_OUTPUT",
        "T6_OUTPUT",
        "observation_path.read_text",
        "google.com",
        "github.com",
        "pypi.org",
        "npmjs.org",
    ):
        assert forbidden not in lower, forbidden


def main() -> None:
    test_review_and_implement_are_deny_by_default()
    test_dependency_allowlist_is_exact_and_deterministic()
    test_invalid_native_policy_fails_closed()
    test_capability_advertising_remains_evidence_gated()
    test_pinned_cli_network_mode_reconciliation()
    test_generated_task_probe_observation_handoff_is_self_contained()
    test_deployed_probe_consumes_common_contract_and_cursor_bypass_checks()
    print("Cursor task egress deterministic regression checks passed")


if __name__ == "__main__":
    main()
