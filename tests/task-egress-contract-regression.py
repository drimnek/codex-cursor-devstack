#!/usr/bin/env python3
"""Unit-test the provider-neutral MA2-SEC-005 task egress contract."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(ROOT / "tests"))

from agentdev.policy.profiles import get_profile  # noqa: E402
from agentdev.policy.resolver import PolicyEscalationError, PolicyResolver  # noqa: E402
from agentdev.policy.schema import ExecutionPolicy  # noqa: E402
from contracts.task_egress import (  # noqa: E402
    ADDRESS_IPV6,
    EGRESS_PROBE_SPECS,
    EgressProbeObservation,
    PROFILE_DEPENDENCY,
    PROFILE_IMPLEMENT,
    PROFILE_REVIEW,
    PROBE_ALLOWED_DESTINATION,
    PROBE_DENIED_PUBLIC,
    PROBE_IPV6_LINK_LOCAL,
    PROBE_IPV6_LOOPBACK,
    PROBE_IPV6_PRIVATE,
    PROBE_IPV6_PUBLIC,
    PROBE_IPV6_RAW_IP_BYPASS,
    PROBE_LOOPBACK_IPV4,
    PROBE_METADATA_IPV4,
    PROBE_NON_ALLOWLISTED_DESTINATION,
    PROBE_PRIVATE_IPV4,
    PROBE_PROVIDER_CONTROL_CONNECTIVITY,
    PROBE_RAW_IP_BYPASS,
    PROBE_REDIRECT_BYPASS,
    evaluate_task_egress_contract,
    task_egress_contract,
)


class FakeAdapter:
    def __init__(self, observations: dict[str, EgressProbeObservation]) -> None:
        self._observations = dict(observations)

    def observation(self, probe_id: str) -> EgressProbeObservation | None:
        return self._observations.get(probe_id)


def expectation_map(profile: str) -> dict[str, bool]:
    return {
        item.probe_id: item.allowed
        for item in task_egress_contract(profile).expectations
    }


def passing_adapter(profile: str) -> FakeAdapter:
    return FakeAdapter(
        {
            probe_id: EgressProbeObservation(
                expected,
                f"synthetic observation for {probe_id}",
            )
            for probe_id, expected in expectation_map(profile).items()
        }
    )


def broad_hardened_baseline() -> ExecutionPolicy:
    return ExecutionPolicy.from_dict(
        {
            "version": 1,
            "workspace": {"access": "write"},
            "reference": {"access": "read"},
            "filesystem": {"external": "write"},
            "network": {"task_shell": {"mode": "allow"}},
            "credentials": {"provider_auth": {"task_shell": "allow"}},
            "git": {"read": True, "commit": True, "push": True},
            "sandbox": {"required": False},
            "resources": {"cpu": 8, "memory": "16g", "pids": 2048},
            "security_class": "hardened",
        }
    )


def resolve_profile(
    profile: str,
    *,
    destinations: tuple[str, ...] | None = None,
) -> ExecutionPolicy:
    return PolicyResolver().resolve(
        platform_baseline=broad_hardened_baseline(),
        execution_profile=get_profile(profile).restrictions(
            task_shell_destinations=destinations
        ),
    )


def test_deny_profiles_are_network_closed() -> None:
    for profile in (PROFILE_REVIEW, PROFILE_IMPLEMENT):
        expected = expectation_map(profile)
        assert expected[PROBE_DENIED_PUBLIC] is False
        assert expected[PROBE_LOOPBACK_IPV4] is False
        assert expected[PROBE_PRIVATE_IPV4] is False
        assert expected[PROBE_METADATA_IPV4] is False
        assert expected[PROBE_RAW_IP_BYPASS] is False
        assert expected[PROBE_PROVIDER_CONTROL_CONNECTIVITY] is True
        assert PROBE_ALLOWED_DESTINATION not in expected
        assert PROBE_NON_ALLOWLISTED_DESTINATION not in expected
        assert PROBE_REDIRECT_BYPASS not in expected

        report = evaluate_task_egress_contract(
            task_egress_contract(profile),
            passing_adapter(profile),
        )
        assert report.passed
        assert report.failures == ()


def test_dependency_allowlist_is_destination_specific() -> None:
    expected = expectation_map(PROFILE_DEPENDENCY)
    assert expected[PROBE_ALLOWED_DESTINATION] is True
    assert expected[PROBE_NON_ALLOWLISTED_DESTINATION] is False
    assert expected[PROBE_LOOPBACK_IPV4] is False
    assert expected[PROBE_PRIVATE_IPV4] is False
    assert expected[PROBE_METADATA_IPV4] is False
    assert expected[PROBE_RAW_IP_BYPASS] is False
    assert expected[PROBE_REDIRECT_BYPASS] is False
    assert expected[PROBE_PROVIDER_CONTROL_CONNECTIVITY] is True
    assert PROBE_DENIED_PUBLIC not in expected

    observations = passing_adapter(PROFILE_DEPENDENCY)._observations
    observations[PROBE_NON_ALLOWLISTED_DESTINATION] = EgressProbeObservation(
        True,
        "simulated non-allowlisted destination bypass",
    )
    report = evaluate_task_egress_contract(
        task_egress_contract(PROFILE_DEPENDENCY),
        FakeAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (
        PROBE_NON_ALLOWLISTED_DESTINATION,
    )


def test_raw_ip_and_redirect_bypass_fail_closed() -> None:
    for probe in (PROBE_RAW_IP_BYPASS, PROBE_REDIRECT_BYPASS):
        observations = passing_adapter(PROFILE_DEPENDENCY)._observations
        observations[probe] = EgressProbeObservation(
            True,
            f"simulated bypass through {probe}",
        )
        report = evaluate_task_egress_contract(
            task_egress_contract(PROFILE_DEPENDENCY),
            FakeAdapter(observations),
        )
        assert not report.passed
        assert tuple(item.probe_id for item in report.failures) == (probe,)


def test_provider_control_connectivity_is_independent_and_required() -> None:
    for profile in (PROFILE_REVIEW, PROFILE_IMPLEMENT, PROFILE_DEPENDENCY):
        observations = passing_adapter(profile)._observations
        observations[PROBE_PROVIDER_CONTROL_CONNECTIVITY] = EgressProbeObservation(
            False,
            "simulated provider control-plane outage",
        )
        report = evaluate_task_egress_contract(
            task_egress_contract(profile),
            FakeAdapter(observations),
        )
        assert not report.passed
        assert tuple(item.probe_id for item in report.failures) == (
            PROBE_PROVIDER_CONTROL_CONNECTIVITY,
        )


def test_ipv6_must_be_observed_or_explicitly_unsupported() -> None:
    ipv6_probes = {
        item.probe_id
        for item in EGRESS_PROBE_SPECS
        if item.address_family == ADDRESS_IPV6
    }
    assert ipv6_probes == {
        PROBE_IPV6_PUBLIC,
        PROBE_IPV6_LOOPBACK,
        PROBE_IPV6_PRIVATE,
        PROBE_IPV6_LINK_LOCAL,
        PROBE_IPV6_RAW_IP_BYPASS,
    }

    for profile in (PROFILE_REVIEW, PROFILE_IMPLEMENT, PROFILE_DEPENDENCY):
        observations = passing_adapter(profile)._observations
        for probe_id in ipv6_probes & observations.keys():
            observations[probe_id] = EgressProbeObservation(
                None,
                "IPv6 explicitly unavailable in this acceptance environment",
            )
        report = evaluate_task_egress_contract(
            task_egress_contract(profile),
            FakeAdapter(observations),
        )
        assert report.passed

    observations = passing_adapter(PROFILE_DEPENDENCY)._observations
    observations[PROBE_PRIVATE_IPV4] = EgressProbeObservation(
        None,
        "simulated unsupported mandatory IPv4 probe",
    )
    report = evaluate_task_egress_contract(
        task_egress_contract(PROFILE_DEPENDENCY),
        FakeAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (
        PROBE_PRIVATE_IPV4,
    )


def test_missing_observation_fails_closed() -> None:
    observations = passing_adapter(PROFILE_DEPENDENCY)._observations
    del observations[PROBE_RAW_IP_BYPASS]
    report = evaluate_task_egress_contract(
        task_egress_contract(PROFILE_DEPENDENCY),
        FakeAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (
        PROBE_RAW_IP_BYPASS,
    )
    assert report.failures[0].detail == "missing probe observation"


def test_contract_validation_is_fail_closed() -> None:
    try:
        task_egress_contract("compatibility")
    except ValueError as exc:
        assert "unsupported task egress profile" in str(exc)
    else:
        raise AssertionError("compatibility received a hardened task-egress contract")

    try:
        EgressProbeObservation(None)
    except ValueError as exc:
        assert "requires detail" in str(exc)
    else:
        raise AssertionError("unsupported probe without diagnostic detail was accepted")


def test_policy_resolution_matches_egress_contract() -> None:
    for profile in (PROFILE_REVIEW, PROFILE_IMPLEMENT):
        resolved = resolve_profile(profile)
        assert resolved.network.task_shell.mode == "deny"
        assert resolved.network.task_shell.destinations == ()

    allowed = "allowed.example.test"
    dependency = resolve_profile(
        PROFILE_DEPENDENCY,
        destinations=(allowed,),
    )
    assert dependency.network.task_shell.mode == "allowlist"
    assert dependency.network.task_shell.destinations == (allowed,)

    try:
        get_profile(PROFILE_DEPENDENCY).restrictions(
            task_shell_destinations=()
        )
    except ValueError as exc:
        assert "requires explicit task-shell destinations" in str(exc)
    else:
        raise AssertionError("dependency profile accepted an empty destination set")

    try:
        PolicyResolver().resolve(
            platform_baseline=broad_hardened_baseline(),
            execution_profile=get_profile(PROFILE_DEPENDENCY).restrictions(
                task_shell_destinations=(allowed,)
            ),
            run_restrictions={
                "network": {
                    "task_shell": {
                        "destinations": [
                            allowed,
                            "non-allowlisted.example.test",
                        ]
                    }
                }
            },
        )
    except PolicyEscalationError as exc:
        assert exc.layer == "run_restrictions"
        assert exc.field == "network.task_shell.destinations"
    else:
        raise AssertionError("run restrictions widened the dependency allowlist")


def test_contract_contains_no_provider_or_endpoint_implementation() -> None:
    source = (ROOT / "tests/contracts/task_egress.py").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in (
        "codex",
        "cursor",
        "podman",
        "sandbox.json",
        "curl ",
        "wget ",
        "http://",
        "https://",
        ".com",
        ".org",
        "127.",
        "169.254.",
        "::1",
    ):
        assert forbidden not in source, forbidden


def main() -> None:
    test_deny_profiles_are_network_closed()
    test_dependency_allowlist_is_destination_specific()
    test_raw_ip_and_redirect_bypass_fail_closed()
    test_provider_control_connectivity_is_independent_and_required()
    test_ipv6_must_be_observed_or_explicitly_unsupported()
    test_missing_observation_fails_closed()
    test_contract_validation_is_fail_closed()
    test_policy_resolution_matches_egress_contract()
    test_contract_contains_no_provider_or_endpoint_implementation()
    print("task egress contract regression checks passed")


if __name__ == "__main__":
    main()
