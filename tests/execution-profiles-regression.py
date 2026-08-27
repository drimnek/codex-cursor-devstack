#!/usr/bin/env python3
"""Regression coverage for MA2-POL-003 built-in execution profiles."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.policy.profiles import get_profile, list_profiles  # noqa: E402
from agentdev.policy.resolver import PolicyEscalationError, PolicyResolver  # noqa: E402
from agentdev.policy.schema import ExecutionPolicy  # noqa: E402


def baseline(*, security_class: str = "compatibility") -> ExecutionPolicy:
    return ExecutionPolicy.from_dict(
        {
            "version": 1,
            "workspace": {"access": "write"},
            "reference": {"access": "read"},
            "filesystem": {"external": "deny"},
            "network": {"task_shell": {"mode": "allow"}},
            "credentials": {"provider_auth": {"task_shell": "allow"}},
            "git": {"read": True, "commit": True, "push": False},
            "sandbox": {"required": False},
            "resources": {"cpu": 8, "memory": "16g", "pids": 2048},
            "security_class": security_class,
        }
    )


def resolve(profile_id: str, *, destinations: list[str] | None = None, security_class: str = "compatibility") -> ExecutionPolicy:
    profile = get_profile(profile_id)
    restrictions = profile.restrictions(task_shell_destinations=destinations)
    return PolicyResolver().resolve(
        platform_baseline=baseline(security_class=security_class),
        execution_profile=restrictions,
    )


def check_registry() -> None:
    assert tuple(profile.id for profile in list_profiles()) == (
        "review",
        "implement",
        "dependency",
        "compatibility",
    )
    assert get_profile("review") is list_profiles()[0]
    try:
        get_profile("missing")
    except ValueError as exc:
        assert "unknown execution profile" in str(exc)
    else:
        raise AssertionError("unknown profile was accepted")

    try:
        get_profile(1)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "profile_id" in str(exc)
    else:
        raise AssertionError("non-string profile ID was accepted")


def check_profile_snapshots() -> None:
    assert get_profile("review").restrictions() == {
        "workspace": {"access": "read"},
        "reference": {"access": "read"},
        "network": {"task_shell": {"mode": "deny"}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"commit": False},
        "sandbox": {"required": True},
    }
    assert get_profile("implement").restrictions() == {
        "workspace": {"access": "write"},
        "reference": {"access": "read"},
        "network": {"task_shell": {"mode": "deny"}},
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"read": True, "commit": True, "push": False},
        "sandbox": {"required": True},
    }
    assert get_profile("dependency").restrictions(
        task_shell_destinations=["pypi.org"]
    ) == {
        "workspace": {"access": "write"},
        "network": {
            "task_shell": {"mode": "allowlist", "destinations": ["pypi.org"]}
        },
        "credentials": {"provider_auth": {"task_shell": "deny"}},
        "git": {"commit": True, "push": False},
        "sandbox": {"required": True},
    }
    assert get_profile("compatibility").restrictions() == {
        "security_class": "compatibility"
    }


def check_review_profile() -> None:
    policy = resolve("review")
    assert policy.workspace.access == "read"
    assert policy.reference.access == "read"
    assert policy.filesystem.external == "deny"
    assert policy.network.task_shell.mode == "deny"
    assert policy.network.task_shell.destinations == ()
    assert policy.credentials.provider_auth.task_shell == "deny"
    assert policy.git.read is True
    assert policy.git.commit is False
    assert policy.git.push is False
    assert policy.sandbox.required is True
    assert policy.resources.memory_bytes == 16 * 1024**3
    assert policy.security_class == "compatibility"

    hardened = resolve("review", security_class="hardened")
    assert hardened.security_class == "hardened"


def check_implement_profile() -> None:
    policy = resolve("implement")
    assert policy.workspace.access == "write"
    assert policy.reference.access == "read"
    assert policy.filesystem.external == "deny"
    assert policy.network.task_shell.mode == "deny"
    assert policy.credentials.provider_auth.task_shell == "deny"
    assert policy.git.read is True
    assert policy.git.commit is True
    assert policy.git.push is False
    assert policy.sandbox.required is True
    assert policy.security_class == "compatibility"


def check_dependency_profile() -> None:
    profile = get_profile("dependency")
    for destinations in (None, []):
        try:
            profile.restrictions(task_shell_destinations=destinations)
        except ValueError as exc:
            assert "requires explicit task-shell destinations" in str(exc)
        else:
            raise AssertionError("dependency profile accepted no destination allowlist")

    policy = resolve(
        "dependency",
        destinations=["registry.npmjs.org", "pypi.org"],
    )
    assert policy.workspace.access == "write"
    assert policy.network.task_shell.mode == "allowlist"
    assert policy.network.task_shell.destinations == (
        "pypi.org",
        "registry.npmjs.org",
    )
    assert policy.credentials.provider_auth.task_shell == "deny"
    assert policy.git.commit is True
    assert policy.git.push is False
    assert policy.sandbox.required is True


def check_compatibility_profile() -> None:
    policy = resolve("compatibility")
    assert policy.security_class == "compatibility"
    assert policy.sandbox.required is False
    assert policy.network.task_shell.mode == "allow"
    assert policy.credentials.provider_auth.task_shell == "allow"

    try:
        resolve("compatibility", security_class="hardened")
    except PolicyEscalationError as exc:
        assert exc.field == "security_class"
    else:
        raise AssertionError("compatibility profile downgraded hardened policy")


def check_profile_inputs_and_immutability() -> None:
    review = get_profile("review")
    first = review.restrictions()
    second = review.restrictions()
    assert first == second
    assert first is not second
    first["workspace"] = {"access": "none"}
    assert review.restrictions()["workspace"] == {"access": "read"}

    try:
        review.restrictions(task_shell_destinations=["pypi.org"])
    except ValueError as exc:
        assert "does not accept" in str(exc)
    else:
        raise AssertionError("non-dependency profile accepted network destinations")


def check_provider_neutral_boundary() -> None:
    source = (PLATFORM_SRC / "agentdev/policy/profiles.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "codex",
        "cursor",
        "/root/.codex",
        "/root/.cursor",
        "podman",
        "subprocess",
        "resolvedexecutionplan",
    ):
        assert forbidden not in lowered, forbidden


def main() -> None:
    check_registry()
    check_profile_snapshots()
    check_review_profile()
    check_implement_profile()
    check_dependency_profile()
    check_compatibility_profile()
    check_profile_inputs_and_immutability()
    check_provider_neutral_boundary()
    print("execution profile regression checks passed")


if __name__ == "__main__":
    main()
