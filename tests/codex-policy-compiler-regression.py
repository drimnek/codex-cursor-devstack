#!/usr/bin/env python3
"""Verify MA2-POL-006 Codex ExecutionPolicy translation and fail-closed limits."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.agents.codex import (
    CODEX_POLICY_COMPILER_BASELINE,
    CodexDriver,
    UnsupportedCodexPolicyError,
    codex_credential_confidentiality_config_argv,
    codex_task_egress_config_argv,
)
from agentdev.core.models import TaskContext
from agentdev.policy.schema import ExecutionPolicy


def policy(
    *,
    workspace: str = "write",
    network: str = "deny",
    destinations: tuple[str, ...] = (),
    security_class: str = "compatibility",
    sandbox_required: bool = True,
    provider_auth: str = "deny",
) -> ExecutionPolicy:
    task_shell: dict[str, object] = {"mode": network}
    if destinations:
        task_shell["destinations"] = list(destinations)
    return ExecutionPolicy.from_dict(
        {
            "version": 1,
            "workspace": {"access": workspace},
            "reference": {"access": "read"},
            "filesystem": {"external": "deny"},
            "network": {"task_shell": task_shell},
            "credentials": {"provider_auth": {"task_shell": provider_auth}},
            "git": {"read": True, "commit": workspace == "write", "push": False},
            "sandbox": {"required": sandbox_required},
            "resources": {"cpu": 4, "memory": "8g", "pids": 1024},
            "security_class": security_class,
        }
    )


def context() -> TaskContext:
    return TaskContext(
        project="project",
        task="task",
        mode="integration",
        status="active",
        metadata_path=Path("/srv/agent-dev/project/tasks/task.json"),
        workspace=Path("/workspace"),
        record={"mode": "integration", "status": "active", "base_commit": "abc"},
    )


def expect_unsupported(value: ExecutionPolicy, fragment: str) -> None:
    try:
        CodexDriver().compile_policy(value)
    except UnsupportedCodexPolicyError as exc:
        assert fragment in str(exc), (str(exc), fragment)
        return
    raise AssertionError(f"expected UnsupportedCodexPolicyError containing {fragment!r}")


def test_compiler_version_baseline() -> None:
    assert CODEX_POLICY_COMPILER_BASELINE == "0.147.0"
    containerfile = (PLATFORM / "containers/Containerfile.codex").read_text(encoding="utf-8")
    assert f"ARG CODEX_VERSION={CODEX_POLICY_COMPILER_BASELINE}" in containerfile


def test_workspace_and_network_translation() -> None:
    driver = CodexDriver()
    read_credentials = codex_credential_confidentiality_config_argv("read")
    write_credentials = codex_credential_confidentiality_config_argv("write")
    deny_network = codex_task_egress_config_argv("deny")
    allowlist_network = codex_task_egress_config_argv(
        "allowlist", ("pypi.org", "registry.npmjs.org")
    )

    review = driver.compile_policy(policy(workspace="read", network="deny"))
    assert review.argv == (
        "--sandbox", "read-only", "-c", "approval_policy=never",
        *read_credentials,
    )

    implement = driver.compile_policy(policy(workspace="write", network="deny"))
    assert implement.argv == (
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        *deny_network,
        *write_credentials,
    )

    unrestricted = driver.compile_policy(
        policy(workspace="write", network="allow", provider_auth="allow")
    )
    assert unrestricted.argv == (
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        "-c",
        "sandbox_workspace_write.network_access=true",
        "-c",
        "features.network_proxy.enabled=false",
    )

    allowlist = driver.compile_policy(
        policy(
            workspace="write",
            network="allowlist",
            destinations=("registry.npmjs.org", "pypi.org"),
        )
    )
    assert allowlist.argv == (
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        *allowlist_network,
        *write_credentials,
    )


def test_unsupported_policy_fails_closed() -> None:
    expect_unsupported(policy(workspace="none"), "workspace.access=none")
    expect_unsupported(
        policy(workspace="read", network="allow"),
        "read-only sandbox cannot enable task-shell network access",
    )
    expect_unsupported(
        policy(security_class="hardened"),
        "security_class:hardened",
    )
    expect_unsupported(
        policy(sandbox_required=False),
        "provider-auth task-shell deny requires provider-native sandboxing",
    )


def test_run_spec_consumes_compiled_policy() -> None:
    driver = CodexDriver()
    compiled = driver.compile_policy(policy(workspace="write", network="deny"))
    run = driver.create_run_spec(context(), compiled, "implement it")
    assert run.argv == (
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        *codex_task_egress_config_argv("deny"),
        *codex_credential_confidentiality_config_argv("write"),
        "implement it",
    )
    assert run.policy_artifacts == compiled


def test_legacy_invocation_contract_is_preserved() -> None:
    driver = CodexDriver()
    cases = (
        ({"readonly": False, "outer_only": False}, "workspace-write"),
        ({"readonly": True, "outer_only": False}, "read-only"),
        ({"readonly": False, "outer_only": True}, "danger-full-access"),
        ({"readonly": True, "outer_only": True}, "danger-full-access"),
    )
    for legacy, sandbox in cases:
        compiled = driver.compile_policy(legacy)
        assert compiled.argv == ("--sandbox", sandbox, "-c", "approval_policy=never")


def test_sec002_and_sec006_are_certified_while_hardened_remains_gated() -> None:
    capabilities = CodexDriver().capabilities()
    assert capabilities.security_classes == frozenset({"compatibility"})
    assert "hardened" not in capabilities.security_classes
    assert capabilities.policy_capabilities == frozenset(
        {"provider_state_protection", "network_deny", "network_allowlist"}
    )


def test_codex_native_keys_stay_out_of_generic_policy_code() -> None:
    forbidden = (
        "sandbox_workspace_write",
        "features.network_proxy",
        "approval_policy=never",
    )
    for path in (PLATFORM / "agentdev/policy").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, (path, marker)


def main() -> None:
    test_compiler_version_baseline()
    test_workspace_and_network_translation()
    test_unsupported_policy_fails_closed()
    test_run_spec_consumes_compiled_policy()
    test_legacy_invocation_contract_is_preserved()
    test_sec002_and_sec006_are_certified_while_hardened_remains_gated()
    test_codex_native_keys_stay_out_of_generic_policy_code()
    print("Codex policy compiler regression checks passed")


if __name__ == "__main__":
    main()
