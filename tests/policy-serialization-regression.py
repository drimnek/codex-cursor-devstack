#!/usr/bin/env python3
"""Regression coverage for MA2-POL-008 canonical policy serialization/hash."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))

from agentdev.policy.schema import ExecutionPolicy
from agentdev.policy.serialization import (
    POLICY_HASH_ALGORITHM,
    POLICY_HASH_PREFIX,
    canonical_policy_bytes,
    canonical_policy_json,
    policy_hash,
)


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}")


def policy_input(*, memory="8g", destinations=None, workspace="write", cpu=4):
    if destinations is None:
        destinations = ["registry.npmjs.org", "pypi.org"]
    return {
        "security_class": "compatibility",
        "resources": {"pids": 1024, "memory": memory, "cpu": cpu},
        "sandbox": {"required": True},
        "git": {"push": False, "commit": True, "read": True},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "network": {
            "task_shell": {
                "destinations": destinations,
                "mode": "allowlist",
            }
        },
        "filesystem": {"external": "deny"},
        "reference": {"access": "read"},
        "workspace": {"access": workspace},
        "version": 1,
    }


def test_canonical_serialization_is_stable() -> None:
    first = ExecutionPolicy.from_dict(policy_input())
    second = ExecutionPolicy.from_dict(
        {
            "version": 1,
            "workspace": {"access": "write"},
            "reference": {"access": "read"},
            "filesystem": {"external": "deny"},
            "network": {
                "task_shell": {
                    "mode": "allowlist",
                    "destinations": ["pypi.org", "registry.npmjs.org"],
                }
            },
            "credentials": {"provider_auth": {"task_shell": "deny"}},
            "git": {"read": True, "commit": True, "push": False},
            "sandbox": {"required": True},
            "resources": {"cpu": 4.0, "memory": 8589934592, "pids": 1024},
            "security_class": "compatibility",
        }
    )

    first_json = canonical_policy_json(first)
    second_json = canonical_policy_json(second)
    assert first_json == second_json
    assert canonical_policy_bytes(first) == canonical_policy_bytes(second)
    assert policy_hash(first) == policy_hash(second)

    decoded = json.loads(first_json)
    assert decoded == first.as_dict()
    assert decoded["version"] == 1
    assert decoded["resources"]["memory"] == 8589934592
    assert decoded["network"]["task_shell"]["destinations"] == [
        "pypi.org",
        "registry.npmjs.org",
    ]


def test_hash_contract_and_effective_change() -> None:
    baseline = ExecutionPolicy.from_dict(policy_input())
    canonical = canonical_policy_bytes(baseline)
    expected_digest = hashlib.sha256(canonical).hexdigest()

    assert POLICY_HASH_ALGORITHM == "sha256"
    assert POLICY_HASH_PREFIX == "sha256:"
    assert policy_hash(baseline) == f"sha256:{expected_digest}"
    assert len(expected_digest) == 64

    readonly = ExecutionPolicy.from_dict(policy_input(workspace="read"))
    lower_cpu = ExecutionPolicy.from_dict(policy_input(cpu=2))
    deny_network = ExecutionPolicy.from_dict(
        {
            **policy_input(),
            "network": {"task_shell": {"mode": "deny"}},
        }
    )
    for changed in (readonly, lower_cpu, deny_network):
        assert canonical_policy_bytes(changed) != canonical
        assert policy_hash(changed) != policy_hash(baseline)


def test_transient_runtime_values_are_outside_fingerprint() -> None:
    policy = ExecutionPolicy.from_dict(policy_input())
    payload = json.loads(canonical_policy_json(policy))
    assert set(payload) == {
        "credentials",
        "filesystem",
        "git",
        "network",
        "reference",
        "resources",
        "sandbox",
        "security_class",
        "version",
        "workspace",
    }
    serialized = canonical_policy_json(policy)
    for transient_name in (
        "agent_id",
        "agent_version",
        "image_id",
        "run_id",
        "started_at",
        "finished_at",
        "workspace_mount",
        "provider_policy_artifacts",
    ):
        assert transient_name not in serialized


def test_strict_input_boundary() -> None:
    expect(TypeError, canonical_policy_json, policy_input())
    expect(TypeError, canonical_policy_bytes, None)
    expect(TypeError, policy_hash, object())


def test_provider_neutral_boundary() -> None:
    source = (PLATFORM / "agentdev/policy/serialization.py").read_text(encoding="utf-8").lower()
    for forbidden in (
        "codex",
        "cursor",
        "podman",
        "subprocess",
        "providerpolicyartifacts",
        "resolvedexecutionplan",
    ):
        assert forbidden not in source


def main() -> None:
    test_canonical_serialization_is_stable()
    test_hash_contract_and_effective_change()
    test_transient_runtime_values_are_outside_fingerprint()
    test_strict_input_boundary()
    test_provider_neutral_boundary()
    print("policy serialization regression checks passed")


if __name__ == "__main__":
    main()
