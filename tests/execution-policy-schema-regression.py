#!/usr/bin/env python3
"""Regression coverage for MA2-POL-001 ExecutionPolicy schema."""
from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.policy.schema import ExecutionPolicy  # noqa: E402


def sample_policy() -> dict[str, object]:
    return {
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
        "resources": {"cpu": 4.0, "memory": "8g", "pids": 1024},
        "security_class": "hardened",
    }


def expect_invalid(value: object, message: str | None = None) -> None:
    try:
        ExecutionPolicy.from_dict(value)
    except ValueError as exc:
        if message is not None:
            assert message in str(exc), (message, str(exc))
    else:
        raise AssertionError("invalid execution policy was accepted")


def check_valid_and_normalized() -> None:
    data = sample_policy()
    data["network"]["task_shell"]["destinations"] = [  # type: ignore[index]
        "registry.npmjs.org",
        "pypi.org",
    ]
    policy = ExecutionPolicy.from_dict(data)
    assert policy.workspace.access == "write"
    assert policy.network.task_shell.destinations == (
        "pypi.org",
        "registry.npmjs.org",
    )
    assert policy.resources.cpu == 4
    assert type(policy.resources.cpu) is int
    assert policy.resources.memory_bytes == 8 * 1024**3
    expected = {
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
        "resources": {"cpu": 4, "memory": 8 * 1024**3, "pids": 1024},
        "security_class": "hardened",
    }
    assert policy.as_dict() == expected
    assert ExecutionPolicy.from_dict(policy.as_dict()) == policy

    equivalent = sample_policy()
    equivalent["resources"]["memory"] = "8192m"  # type: ignore[index]
    assert ExecutionPolicy.from_dict(equivalent).resources.memory_bytes == 8 * 1024**3
    equivalent["resources"]["memory"] = "8G"  # type: ignore[index]
    assert ExecutionPolicy.from_dict(equivalent).resources.memory_bytes == 8 * 1024**3
    equivalent["resources"]["memory"] = 8 * 1024**3  # type: ignore[index]
    assert ExecutionPolicy.from_dict(equivalent).resources.memory_bytes == 8 * 1024**3

    deny = sample_policy()
    deny["network"] = {"task_shell": {"mode": "deny"}}
    assert ExecutionPolicy.from_dict(deny).network.task_shell.destinations == ()

    compatibility = sample_policy()
    compatibility["network"] = {"task_shell": {"mode": "allow"}}
    compatibility["credentials"] = {"provider_auth": {"task_shell": "allow"}}
    compatibility["sandbox"] = {"required": False}
    compatibility["security_class"] = "compatibility"
    assert ExecutionPolicy.from_dict(compatibility).security_class == "compatibility"


def check_recursive_unknown_field_rejection() -> None:
    cases: list[tuple[list[str], str]] = [
        (["unexpected"], "policy contains unknown fields"),
        (["workspace", "unexpected"], "workspace contains unknown fields"),
        (["network", "unexpected"], "network contains unknown fields"),
        (
            ["network", "task_shell", "unexpected"],
            "network.task_shell contains unknown fields",
        ),
        (["credentials", "unexpected"], "credentials contains unknown fields"),
        (
            ["credentials", "provider_auth", "unexpected"],
            "credentials.provider_auth contains unknown fields",
        ),
        (["resources", "unexpected"], "resources contains unknown fields"),
    ]
    for path, message in cases:
        data = sample_policy()
        target = data
        for key in path[:-1]:
            target = target[key]  # type: ignore[index,assignment]
        target[path[-1]] = True  # type: ignore[index]
        expect_invalid(data, message)


def check_invalid_values() -> None:
    mutations = [
        ("version", 2),
        ("workspace.access", "execute"),
        ("reference.access", "write"),
        ("filesystem.external", "maybe"),
        ("network.task_shell.mode", "provider_required"),
        ("credentials.provider_auth.task_shell", "hidden"),
        ("git.read", 1),
        ("sandbox.required", "yes"),
        ("resources.cpu", 0),
        ("resources.cpu", float("inf")),
        ("resources.pids", 0),
        ("security_class", "secure"),
    ]
    for dotted, value in mutations:
        data = sample_policy()
        target = data
        parts = dotted.split(".")
        for key in parts[:-1]:
            target = target[key]  # type: ignore[index,assignment]
        target[parts[-1]] = value  # type: ignore[index]
        expect_invalid(data)

    for memory in (0, -1, True, "0", "potato", "1.5g", "8gb", " 8g", "8g "):
        data = sample_policy()
        data["resources"]["memory"] = memory  # type: ignore[index]
        expect_invalid(data, "resources.memory")

    data = sample_policy()
    data["network"] = {"task_shell": {"mode": "allowlist"}}
    expect_invalid(data, "requires destinations")

    data = sample_policy()
    data["network"] = {
        "task_shell": {"mode": "deny", "destinations": ["pypi.org"]}
    }
    expect_invalid(data, "only valid in allowlist mode")

    data = sample_policy()
    data["network"]["task_shell"]["destinations"] = ["pypi.org", "pypi.org"]  # type: ignore[index]
    expect_invalid(data, "duplicate destination")

    missing = sample_policy()
    del missing["sandbox"]
    expect_invalid(missing, "missing required fields")


def check_provider_neutral_boundary() -> None:
    source = (PLATFORM_SRC / "agentdev/policy/schema.py").read_text(encoding="utf-8")
    for provider_literal in ("/root/.codex", "/root/.cursor", "codex exec", "agent --trust"):
        assert provider_literal not in source
    assert "subprocess" not in source
    assert "podman" not in source.lower()


def main() -> None:
    check_valid_and_normalized()
    check_recursive_unknown_field_rejection()
    check_invalid_values()
    check_provider_neutral_boundary()
    print("execution policy schema regression checks passed")


if __name__ == "__main__":
    main()
