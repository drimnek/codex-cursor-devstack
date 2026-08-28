#!/usr/bin/env python3
"""Unit-test the provider-neutral MA2-SEC-001 hardened contract harness."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from contracts.hardened_security import (
    ALL_PROBES,
    PROBE_ARBITRARY_INTERNET,
    PROBE_CONTROL_PROCESS_CMDLINE_READ,
    PROBE_CONTROL_PROCESS_ENVIRON_READ,
    PROBE_CONTROL_PROCESS_FD_ACCESS,
    PROBE_CONTROL_PROCESS_FS_TRAVERSE,
    PROBE_CONTROL_PROCESS_MEMORY_READ,
    PROBE_EXTERNAL_FILESYSTEM_WRITE,
    PROBE_GIT_COMMIT,
    PROBE_HOST_CREDENTIALS_READ,
    PROBE_HUMAN_CHECKOUT_READ,
    PROBE_LOOPBACK_NETWORK,
    PROBE_METADATA_NETWORK,
    PROBE_PRIVATE_NETWORK,
    PROBE_PROVIDER_AUTH_READ,
    PROBE_RUNTIME_SOCKET_ACCESS,
    PROBE_TESTS_RUN,
    PROBE_WORKSPACE_READ,
    PROBE_WORKSPACE_WRITE,
    ProbeObservation,
    evaluate_hardened_security_contract,
    hardened_security_contract,
)


class FakeResultAdapter:
    def __init__(self, observations: dict[str, ProbeObservation]) -> None:
        self._observations = dict(observations)

    def observation(self, probe_id: str) -> ProbeObservation | None:
        return self._observations.get(probe_id)


def expectation_map(profile: str) -> dict[str, bool]:
    return {
        item.probe_id: item.allowed
        for item in hardened_security_contract(profile).expectations
    }


def passing_adapter(profile: str) -> FakeResultAdapter:
    return FakeResultAdapter(
        {
            probe: ProbeObservation(allowed, f"fake observation for {probe}")
            for probe, allowed in expectation_map(profile).items()
        }
    )


def test_profile_expectations() -> None:
    review = expectation_map("review")
    implement = expectation_map("implement")
    dependency = expectation_map("dependency")

    for expected in (review, implement, dependency):
        assert tuple(expected) == ALL_PROBES
        assert expected[PROBE_WORKSPACE_READ]
        assert expected[PROBE_TESTS_RUN]
        for probe in (
            PROBE_HUMAN_CHECKOUT_READ,
            PROBE_HOST_CREDENTIALS_READ,
            PROBE_PROVIDER_AUTH_READ,
            PROBE_CONTROL_PROCESS_ENVIRON_READ,
            PROBE_CONTROL_PROCESS_CMDLINE_READ,
            PROBE_CONTROL_PROCESS_FD_ACCESS,
            PROBE_CONTROL_PROCESS_FS_TRAVERSE,
            PROBE_CONTROL_PROCESS_MEMORY_READ,
            PROBE_EXTERNAL_FILESYSTEM_WRITE,
            PROBE_RUNTIME_SOCKET_ACCESS,
            PROBE_ARBITRARY_INTERNET,
            PROBE_PRIVATE_NETWORK,
            PROBE_LOOPBACK_NETWORK,
            PROBE_METADATA_NETWORK,
        ):
            assert expected[probe] is False

    assert review[PROBE_WORKSPACE_WRITE] is False
    assert review[PROBE_GIT_COMMIT] is False
    assert implement[PROBE_WORKSPACE_WRITE] is True
    assert implement[PROBE_GIT_COMMIT] is True
    assert dependency[PROBE_WORKSPACE_WRITE] is True
    assert dependency[PROBE_GIT_COMMIT] is True


def test_control_process_procfs_channels_are_explicitly_denied() -> None:
    expected = expectation_map("implement")
    procfs_probes = (
        PROBE_CONTROL_PROCESS_ENVIRON_READ,
        PROBE_CONTROL_PROCESS_CMDLINE_READ,
        PROBE_CONTROL_PROCESS_FD_ACCESS,
        PROBE_CONTROL_PROCESS_FS_TRAVERSE,
        PROBE_CONTROL_PROCESS_MEMORY_READ,
    )
    assert all(expected[probe] is False for probe in procfs_probes)
    assert all(probe in ALL_PROBES for probe in procfs_probes)


def test_fake_adapter_passes_every_profile() -> None:
    for profile in ("review", "implement", "dependency"):
        contract = hardened_security_contract(profile)
        report = evaluate_hardened_security_contract(contract, passing_adapter(profile))
        assert report.profile == profile
        assert report.passed
        assert report.failures == ()
        assert len(report.checks) == len(ALL_PROBES)


def test_forbidden_success_is_reported() -> None:
    profile = "implement"
    observations = passing_adapter(profile)._observations
    observations[PROBE_PROVIDER_AUTH_READ] = ProbeObservation(
        True,
        "simulated provider credential exposure",
    )
    report = evaluate_hardened_security_contract(
        hardened_security_contract(profile),
        FakeResultAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (PROBE_PROVIDER_AUTH_READ,)
    assert report.failures[0].expected_allowed is False
    assert report.failures[0].observed_succeeded is True


def test_required_capability_failure_is_reported() -> None:
    profile = "review"
    observations = passing_adapter(profile)._observations
    observations[PROBE_WORKSPACE_READ] = ProbeObservation(False, "simulated read failure")
    report = evaluate_hardened_security_contract(
        hardened_security_contract(profile),
        FakeResultAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (PROBE_WORKSPACE_READ,)


def test_missing_observation_fails_closed() -> None:
    profile = "dependency"
    observations = passing_adapter(profile)._observations
    del observations[PROBE_RUNTIME_SOCKET_ACCESS]
    report = evaluate_hardened_security_contract(
        hardened_security_contract(profile),
        FakeResultAdapter(observations),
    )
    assert not report.passed
    assert tuple(item.probe_id for item in report.failures) == (PROBE_RUNTIME_SOCKET_ACCESS,)
    assert report.failures[0].observed_succeeded is None
    assert report.failures[0].detail == "missing probe observation"


def test_compatibility_is_not_a_hardened_contract() -> None:
    try:
        hardened_security_contract("compatibility")
    except ValueError as exc:
        assert "unsupported hardened security profile" in str(exc)
    else:
        raise AssertionError("compatibility unexpectedly received a hardened contract")


def test_generic_contract_contains_no_provider_identity_or_paths() -> None:
    source = (ROOT / "tests/contracts/hardened_security.py").read_text(encoding="utf-8").lower()
    for forbidden in (
        "codex",
        "cursor",
        ".codex",
        ".cursor",
        "/root/",
        "provider_state_target",
    ):
        assert forbidden not in source, forbidden


def main() -> None:
    test_profile_expectations()
    test_control_process_procfs_channels_are_explicitly_denied()
    test_fake_adapter_passes_every_profile()
    test_forbidden_success_is_reported()
    test_required_capability_failure_is_reported()
    test_missing_observation_fails_closed()
    test_compatibility_is_not_a_hardened_contract()
    test_generic_contract_contains_no_provider_identity_or_paths()
    print("hardened security contract regression checks passed")


if __name__ == "__main__":
    main()
