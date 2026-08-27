#!/usr/bin/env python3
"""Regression coverage for MA2-POL-002 monotonic PolicyResolver."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.policy.resolver import PolicyEscalationError, PolicyResolver  # noqa: E402
from agentdev.policy.schema import ExecutionPolicy  # noqa: E402


def baseline(**changes: object) -> ExecutionPolicy:
    data: dict[str, object] = {
        "version": 1,
        "workspace": {"access": "write"},
        "reference": {"access": "read"},
        "filesystem": {"external": "write"},
        "network": {"task_shell": {"mode": "allow"}},
        "credentials": {"provider_auth": {"task_shell": "allow"}},
        "git": {"read": True, "commit": True, "push": True},
        "sandbox": {"required": False},
        "resources": {"cpu": 8, "memory": "16g", "pids": 2048},
        "security_class": "compatibility",
    }
    for dotted, value in changes.items():
        target = data
        parts = dotted.split("__")
        for key in parts[:-1]:
            target = target[key]  # type: ignore[index,assignment]
        target[parts[-1]] = value  # type: ignore[index]
    return ExecutionPolicy.from_dict(data)


def resolve_pair(
    upper: ExecutionPolicy,
    restrictions: dict[str, object],
) -> ExecutionPolicy:
    return PolicyResolver().resolve(
        platform_baseline=upper,
        project_policy=restrictions,
    )


def expect_escalation(
    upper: ExecutionPolicy,
    restrictions: dict[str, object],
    field: str,
) -> None:
    try:
        resolve_pair(upper, restrictions)
    except PolicyEscalationError as exc:
        assert exc.layer == "project_policy", exc
        assert exc.field == field, (field, exc.field, str(exc))
        assert field in str(exc)
    else:
        raise AssertionError(f"widening for {field} was accepted")


def nested(field: str, value: object) -> dict[str, object]:
    parts = field.split(".")
    result: object = value
    for part in reversed(parts):
        result = {part: result}
    return result  # type: ignore[return-value]


def policy_value(policy: ExecutionPolicy, field: str) -> object:
    value: object = policy
    for part in field.split("."):
        value = getattr(value, part)
    return value


def check_ranked_fields() -> None:
    matrices = (
        ("workspace.access", ("none", "read", "write")),
        ("reference.access", ("none", "read")),
        ("filesystem.external", ("deny", "read", "write")),
        ("credentials.provider_auth.task_shell", ("deny", "allow")),
    )
    for field, ordered in matrices:
        mutation = field.replace(".", "__")
        for upper_index, lower_index in itertools.product(
            range(len(ordered)), repeat=2
        ):
            upper = baseline(**{mutation: ordered[upper_index]})
            restrictions = nested(field, ordered[lower_index])
            if lower_index <= upper_index:
                resolved = resolve_pair(upper, restrictions)
                assert policy_value(resolved, field) == ordered[lower_index]
            else:
                expect_escalation(upper, restrictions, field)


def check_network_matrix() -> None:
    deny = baseline(network__task_shell={"mode": "deny"})
    allow = baseline(network__task_shell={"mode": "allow"})
    broad = baseline(
        network__task_shell={
            "mode": "allowlist",
            "destinations": ["pypi.org", "registry.npmjs.org"],
        }
    )

    narrowed = resolve_pair(
        allow,
        {
            "network": {
                "task_shell": {
                    "mode": "allowlist",
                    "destinations": ["registry.npmjs.org", "pypi.org"],
                }
            }
        },
    )
    assert narrowed.network.task_shell.destinations == (
        "pypi.org",
        "registry.npmjs.org",
    )

    subset = resolve_pair(
        broad,
        {"network": {"task_shell": {"destinations": ["pypi.org"]}}},
    )
    assert subset.network.task_shell.destinations == ("pypi.org",)

    denied = resolve_pair(broad, {"network": {"task_shell": {"mode": "deny"}}})
    assert denied.network.task_shell.mode == "deny"
    assert denied.network.task_shell.destinations == ()

    expect_escalation(
        deny,
        {
            "network": {
                "task_shell": {
                    "mode": "allowlist",
                    "destinations": ["pypi.org"],
                }
            }
        },
        "network.task_shell.mode",
    )
    expect_escalation(
        broad,
        {"network": {"task_shell": {"mode": "allow"}}},
        "network.task_shell.mode",
    )
    expect_escalation(
        broad,
        {"network": {"task_shell": {"destinations": ["example.com"]}}},
        "network.task_shell.destinations",
    )


def check_boolean_semantics() -> None:
    for field in ("read", "commit", "push"):
        broad = baseline(**{f"git__{field}": True})
        narrow = baseline(**{f"git__{field}": False})
        assert resolve_pair(broad, {"git": {field: False}}).git.as_dict()[field] is False
        expect_escalation(narrow, {"git": {field: True}}, f"git.{field}")

    optional = baseline(sandbox__required=False)
    required = baseline(sandbox__required=True)
    assert resolve_pair(optional, {"sandbox": {"required": True}}).sandbox.required
    expect_escalation(required, {"sandbox": {"required": False}}, "sandbox.required")


def check_resource_limits() -> None:
    broad = baseline(resources__cpu=8, resources__memory="16g", resources__pids=2048)
    resolved = resolve_pair(
        broad,
        {"resources": {"cpu": 4, "memory": "8192m", "pids": 1024}},
    )
    assert resolved.resources.cpu == 4
    assert resolved.resources.memory_bytes == 8 * 1024**3
    assert resolved.resources.pids == 1024

    expect_escalation(
        baseline(resources__cpu=4),
        {"resources": {"cpu": 8}},
        "resources.cpu",
    )
    expect_escalation(
        baseline(resources__memory="8g"),
        {"resources": {"memory": "16g"}},
        "resources.memory",
    )
    expect_escalation(
        baseline(resources__pids=1024),
        {"resources": {"pids": 2048}},
        "resources.pids",
    )


def check_security_class() -> None:
    compatibility = baseline(security_class="compatibility")
    hardened = baseline(security_class="hardened")
    assert (
        resolve_pair(compatibility, {"security_class": "hardened"}).security_class
        == "hardened"
    )
    expect_escalation(hardened, {"security_class": "compatibility"}, "security_class")


def check_layer_composition_and_hard_denials() -> None:
    resolver = PolicyResolver()
    platform = baseline()
    resolved = resolver.resolve(
        platform_baseline=platform,
        project_policy={
            "network": {
                "task_shell": {
                    "mode": "allowlist",
                    "destinations": ["pypi.org", "registry.npmjs.org"],
                }
            },
            "resources": {"cpu": 6},
        },
        execution_profile={
            "workspace": {"access": "read"},
            "network": {"task_shell": {"destinations": ["pypi.org"]}},
            "sandbox": {"required": True},
        },
        run_restrictions={
            "network": {"task_shell": {"mode": "deny"}},
            "resources": {"cpu": 4, "pids": 1024},
            "security_class": "hardened",
        },
    )
    assert resolved.workspace.access == "read"
    assert resolved.network.task_shell.mode == "deny"
    assert resolved.network.task_shell.destinations == ()
    assert resolved.sandbox.required is True
    assert resolved.resources.cpu == 4
    assert resolved.resources.memory_bytes == 16 * 1024**3
    assert resolved.resources.pids == 1024
    assert resolved.security_class == "hardened"

    hard_deny = baseline(network__task_shell={"mode": "deny"})
    try:
        resolver.resolve(
            platform_baseline=hard_deny,
            project_policy={"workspace": {"access": "read"}},
            execution_profile={
                "network": {
                    "task_shell": {
                        "mode": "allowlist",
                        "destinations": ["pypi.org"],
                    }
                }
            },
        )
    except PolicyEscalationError as exc:
        assert exc.layer == "execution_profile"
        assert exc.field == "network.task_shell.mode"
    else:
        raise AssertionError("lower layer widened platform hard network denial")


def check_sparse_inheritance_and_invalid_layers() -> None:
    platform = baseline()
    resolver = PolicyResolver()
    assert resolver.resolve(platform_baseline=platform) is platform

    resolved = resolver.resolve(
        platform_baseline=platform,
        project_policy={"workspace": {"access": "read"}},
    )
    expected = platform.as_dict()
    expected["workspace"] = {"access": "read"}
    assert resolved.as_dict() == expected

    for invalid in (
        {"unexpected": True},
        {"network": {"unexpected": True}},
        {"resources": {"unexpected": 1}},
    ):
        try:
            resolver.resolve(platform_baseline=platform, project_policy=invalid)
        except ValueError as exc:
            assert "unknown policy field" in str(exc)
        else:
            raise AssertionError(f"unknown sparse policy field accepted: {invalid!r}")

    try:
        resolver.resolve(platform_baseline=platform, project_policy=[])  # type: ignore[arg-type]
    except TypeError as exc:
        assert "project_policy" in str(exc)
    else:
        raise AssertionError("non-mapping sparse policy layer was accepted")

    try:
        resolver.resolve(
            platform_baseline=platform,
            project_policy={"network": {"task_shell": {"mode": "allowlist"}}},
        )
    except ValueError as exc:
        assert "requires destinations" in str(exc)
    else:
        raise AssertionError("allowlist without destinations was accepted")


def check_determinism_and_input_immutability() -> None:
    platform = baseline()
    restrictions: dict[str, object] = {
        "network": {
            "task_shell": {
                "mode": "allowlist",
                "destinations": ["registry.npmjs.org", "pypi.org"],
            }
        },
        "resources": {"memory": "8192m"},
    }
    original = repr(restrictions)
    resolver = PolicyResolver()
    first = resolver.resolve(platform_baseline=platform, project_policy=restrictions)
    second = resolver.resolve(platform_baseline=platform, project_policy=restrictions)
    assert first == second
    assert first.as_dict() == second.as_dict()
    assert repr(restrictions) == original


def check_provider_neutral_boundary() -> None:
    source = (PLATFORM_SRC / "agentdev/policy/resolver.py").read_text(encoding="utf-8")
    for provider_literal in ("codex", "cursor", "/root/.codex", "/root/.cursor"):
        assert provider_literal not in source.lower()
    assert "podman" not in source.lower()
    assert "subprocess" not in source
    assert "ResolvedExecutionPlan" not in source


def main() -> None:
    check_ranked_fields()
    check_network_matrix()
    check_boolean_semantics()
    check_resource_limits()
    check_security_class()
    check_layer_composition_and_hard_denials()
    check_sparse_inheritance_and_invalid_layers()
    check_determinism_and_input_immutability()
    check_provider_neutral_boundary()
    print("policy resolver regression checks passed")


if __name__ == "__main__":
    main()
