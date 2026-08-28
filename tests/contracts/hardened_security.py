"""Provider-neutral hardened executor security contract.

This module defines observable acceptance probes only. It does not know provider
state paths, provider CLI syntax, Podman arguments, or native sandbox formats.
Provider-specific T6 adapters execute the probes and return observations through
``SecurityResultAdapter``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


PROFILE_REVIEW = "review"
PROFILE_IMPLEMENT = "implement"
PROFILE_DEPENDENCY = "dependency"
HARDENED_PROFILES = frozenset({PROFILE_REVIEW, PROFILE_IMPLEMENT, PROFILE_DEPENDENCY})

PROBE_WORKSPACE_READ = "workspace.read"
PROBE_WORKSPACE_WRITE = "workspace.write"
PROBE_TESTS_RUN = "tests.run"
PROBE_GIT_COMMIT = "git.commit"
PROBE_HUMAN_CHECKOUT_READ = "human_checkout.read"
PROBE_HOST_CREDENTIALS_READ = "host_credentials.read"
PROBE_PROVIDER_AUTH_READ = "provider_auth.read"
PROBE_EXTERNAL_FILESYSTEM_WRITE = "filesystem.external_write"
PROBE_RUNTIME_SOCKET_ACCESS = "runtime_socket.access"
PROBE_ARBITRARY_INTERNET = "network.arbitrary_internet"
PROBE_PRIVATE_NETWORK = "network.private"
PROBE_LOOPBACK_NETWORK = "network.loopback"
PROBE_METADATA_NETWORK = "network.metadata"

ALL_PROBES = (
    PROBE_WORKSPACE_READ,
    PROBE_WORKSPACE_WRITE,
    PROBE_TESTS_RUN,
    PROBE_GIT_COMMIT,
    PROBE_HUMAN_CHECKOUT_READ,
    PROBE_HOST_CREDENTIALS_READ,
    PROBE_PROVIDER_AUTH_READ,
    PROBE_EXTERNAL_FILESYSTEM_WRITE,
    PROBE_RUNTIME_SOCKET_ACCESS,
    PROBE_ARBITRARY_INTERNET,
    PROBE_PRIVATE_NETWORK,
    PROBE_LOOPBACK_NETWORK,
    PROBE_METADATA_NETWORK,
)


@dataclass(frozen=True, slots=True)
class SecurityExpectation:
    probe_id: str
    allowed: bool

    def __post_init__(self) -> None:
        if self.probe_id not in ALL_PROBES:
            raise ValueError(f"unknown hardened security probe {self.probe_id!r}")
        if type(self.allowed) is not bool:
            raise ValueError("security expectation allowed must be boolean")


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    succeeded: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise ValueError("probe observation succeeded must be boolean")
        if not isinstance(self.detail, str):
            raise ValueError("probe observation detail must be a string")


class SecurityResultAdapter(Protocol):
    """Provider/runtime-specific result source consumed by the common contract."""

    def observation(self, probe_id: str) -> ProbeObservation | None:
        """Return one completed probe observation or ``None`` if it is missing."""


@dataclass(frozen=True, slots=True)
class HardenedSecurityContract:
    profile: str
    expectations: tuple[SecurityExpectation, ...]

    def __post_init__(self) -> None:
        if self.profile not in HARDENED_PROFILES:
            raise ValueError(f"unsupported hardened security profile {self.profile!r}")
        probe_ids = tuple(item.probe_id for item in self.expectations)
        if probe_ids != ALL_PROBES:
            raise ValueError("hardened security contract must define every probe exactly once")


@dataclass(frozen=True, slots=True)
class ContractCheck:
    probe_id: str
    expected_allowed: bool
    observed_succeeded: bool | None
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ContractReport:
    profile: str
    checks: tuple[ContractCheck, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.checks)

    @property
    def failures(self) -> tuple[ContractCheck, ...]:
        return tuple(item for item in self.checks if not item.passed)


def hardened_security_contract(profile: str) -> HardenedSecurityContract:
    """Return the common hardened expectations for one execution profile."""
    if profile not in HARDENED_PROFILES:
        raise ValueError(f"unsupported hardened security profile {profile!r}")

    workspace_write = profile in {PROFILE_IMPLEMENT, PROFILE_DEPENDENCY}
    git_commit = profile in {PROFILE_IMPLEMENT, PROFILE_DEPENDENCY}
    allowed = {
        PROBE_WORKSPACE_READ: True,
        PROBE_WORKSPACE_WRITE: workspace_write,
        PROBE_TESTS_RUN: True,
        PROBE_GIT_COMMIT: git_commit,
        PROBE_HUMAN_CHECKOUT_READ: False,
        PROBE_HOST_CREDENTIALS_READ: False,
        PROBE_PROVIDER_AUTH_READ: False,
        PROBE_EXTERNAL_FILESYSTEM_WRITE: False,
        PROBE_RUNTIME_SOCKET_ACCESS: False,
        PROBE_ARBITRARY_INTERNET: False,
        PROBE_PRIVATE_NETWORK: False,
        PROBE_LOOPBACK_NETWORK: False,
        PROBE_METADATA_NETWORK: False,
    }
    return HardenedSecurityContract(
        profile=profile,
        expectations=tuple(SecurityExpectation(probe, allowed[probe]) for probe in ALL_PROBES),
    )


def evaluate_hardened_security_contract(
    contract: HardenedSecurityContract,
    adapter: SecurityResultAdapter,
) -> ContractReport:
    """Evaluate completed probe observations against the common contract."""
    checks: list[ContractCheck] = []
    for expectation in contract.expectations:
        observation = adapter.observation(expectation.probe_id)
        if observation is None:
            checks.append(
                ContractCheck(
                    probe_id=expectation.probe_id,
                    expected_allowed=expectation.allowed,
                    observed_succeeded=None,
                    passed=False,
                    detail="missing probe observation",
                )
            )
            continue
        if not isinstance(observation, ProbeObservation):
            raise TypeError(
                f"adapter observation for {expectation.probe_id!r} must be ProbeObservation or None"
            )
        passed = observation.succeeded is expectation.allowed
        checks.append(
            ContractCheck(
                probe_id=expectation.probe_id,
                expected_allowed=expectation.allowed,
                observed_succeeded=observation.succeeded,
                passed=passed,
                detail=observation.detail,
            )
        )
    return ContractReport(profile=contract.profile, checks=tuple(checks))
