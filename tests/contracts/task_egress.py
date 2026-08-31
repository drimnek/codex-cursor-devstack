"""Provider-neutral MA2-SEC-005 task-shell egress acceptance contract.

The contract names observable network outcomes only. Provider-specific adapters
choose controlled endpoints and execution mechanics; this module contains no
provider CLI, sandbox, runtime-backend, hostname, URL, or concrete IP knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


PROFILE_REVIEW = "review"
PROFILE_IMPLEMENT = "implement"
PROFILE_DEPENDENCY = "dependency"
EGRESS_PROFILES = frozenset(
    {PROFILE_REVIEW, PROFILE_IMPLEMENT, PROFILE_DEPENDENCY}
)

SCOPE_TASK_SHELL = "task-shell"
SCOPE_PROVIDER_CONTROL = "provider-control"
ADDRESS_AGNOSTIC = "agnostic"
ADDRESS_IPV4 = "ipv4"
ADDRESS_IPV6 = "ipv6"

PROBE_DENIED_PUBLIC = "network.deny.public_destination"
PROBE_ALLOWED_DESTINATION = "network.allowlist.allowed_destination"
PROBE_NON_ALLOWLISTED_DESTINATION = "network.allowlist.non_allowlisted_destination"
PROBE_LOOPBACK_IPV4 = "network.ipv4.loopback"
PROBE_PRIVATE_IPV4 = "network.ipv4.private"
PROBE_METADATA_IPV4 = "network.ipv4.metadata"
PROBE_RAW_IP_BYPASS = "network.raw_ip_bypass"
PROBE_IPV6_PUBLIC = "network.ipv6.public_destination"
PROBE_IPV6_LOOPBACK = "network.ipv6.loopback"
PROBE_IPV6_PRIVATE = "network.ipv6.private"
PROBE_IPV6_LINK_LOCAL = "network.ipv6.link_local"
PROBE_IPV6_RAW_IP_BYPASS = "network.ipv6.raw_ip_bypass"
PROBE_REDIRECT_BYPASS = "network.allowlist.redirect_bypass"
PROBE_PROVIDER_CONTROL_CONNECTIVITY = "provider_control.network"


@dataclass(frozen=True, slots=True)
class EgressProbeSpec:
    """One provider-neutral probe role consumed by provider/runtime adapters."""

    probe_id: str
    scope: str
    target_class: str
    address_family: str
    optional_if_unsupported: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.probe_id, str) or not self.probe_id:
            raise ValueError("egress probe_id must be a non-empty string")
        if self.scope not in {SCOPE_TASK_SHELL, SCOPE_PROVIDER_CONTROL}:
            raise ValueError(f"unsupported egress probe scope {self.scope!r}")
        if not isinstance(self.target_class, str) or not self.target_class:
            raise ValueError("egress target_class must be a non-empty string")
        if self.address_family not in {
            ADDRESS_AGNOSTIC,
            ADDRESS_IPV4,
            ADDRESS_IPV6,
        }:
            raise ValueError(
                f"unsupported egress address family {self.address_family!r}"
            )
        if type(self.optional_if_unsupported) is not bool:
            raise ValueError("optional_if_unsupported must be boolean")


EGRESS_PROBE_SPECS = (
    EgressProbeSpec(
        PROBE_DENIED_PUBLIC,
        SCOPE_TASK_SHELL,
        "controlled-public",
        ADDRESS_AGNOSTIC,
    ),
    EgressProbeSpec(
        PROBE_ALLOWED_DESTINATION,
        SCOPE_TASK_SHELL,
        "explicit-allowlist",
        ADDRESS_AGNOSTIC,
    ),
    EgressProbeSpec(
        PROBE_NON_ALLOWLISTED_DESTINATION,
        SCOPE_TASK_SHELL,
        "non-allowlisted-public",
        ADDRESS_AGNOSTIC,
    ),
    EgressProbeSpec(
        PROBE_LOOPBACK_IPV4,
        SCOPE_TASK_SHELL,
        "loopback",
        ADDRESS_IPV4,
    ),
    EgressProbeSpec(
        PROBE_PRIVATE_IPV4,
        SCOPE_TASK_SHELL,
        "private",
        ADDRESS_IPV4,
    ),
    EgressProbeSpec(
        PROBE_METADATA_IPV4,
        SCOPE_TASK_SHELL,
        "link-local-metadata",
        ADDRESS_IPV4,
    ),
    EgressProbeSpec(
        PROBE_RAW_IP_BYPASS,
        SCOPE_TASK_SHELL,
        "raw-ip-public",
        ADDRESS_IPV4,
    ),
    EgressProbeSpec(
        PROBE_IPV6_PUBLIC,
        SCOPE_TASK_SHELL,
        "non-allowlisted-public",
        ADDRESS_IPV6,
        optional_if_unsupported=True,
    ),
    EgressProbeSpec(
        PROBE_IPV6_LOOPBACK,
        SCOPE_TASK_SHELL,
        "loopback",
        ADDRESS_IPV6,
        optional_if_unsupported=True,
    ),
    EgressProbeSpec(
        PROBE_IPV6_PRIVATE,
        SCOPE_TASK_SHELL,
        "private",
        ADDRESS_IPV6,
        optional_if_unsupported=True,
    ),
    EgressProbeSpec(
        PROBE_IPV6_LINK_LOCAL,
        SCOPE_TASK_SHELL,
        "link-local",
        ADDRESS_IPV6,
        optional_if_unsupported=True,
    ),
    EgressProbeSpec(
        PROBE_IPV6_RAW_IP_BYPASS,
        SCOPE_TASK_SHELL,
        "raw-ip-public",
        ADDRESS_IPV6,
        optional_if_unsupported=True,
    ),
    EgressProbeSpec(
        PROBE_REDIRECT_BYPASS,
        SCOPE_TASK_SHELL,
        "allowed-origin-redirect-to-denied",
        ADDRESS_AGNOSTIC,
    ),
    EgressProbeSpec(
        PROBE_PROVIDER_CONTROL_CONNECTIVITY,
        SCOPE_PROVIDER_CONTROL,
        "provider-api",
        ADDRESS_AGNOSTIC,
    ),
)
PROBE_SPEC_BY_ID = {item.probe_id: item for item in EGRESS_PROBE_SPECS}
if len(PROBE_SPEC_BY_ID) != len(EGRESS_PROBE_SPECS):
    raise RuntimeError("task egress probe IDs must be unique")


@dataclass(frozen=True, slots=True)
class EgressExpectation:
    probe_id: str
    allowed: bool

    def __post_init__(self) -> None:
        if self.probe_id not in PROBE_SPEC_BY_ID:
            raise ValueError(f"unknown task egress probe {self.probe_id!r}")
        if type(self.allowed) is not bool:
            raise ValueError("egress expectation allowed must be boolean")


@dataclass(frozen=True, slots=True)
class EgressProbeObservation:
    """One adapter observation.

    ``succeeded=None`` explicitly records an unsupported probe. Only probe specs
    marked ``optional_if_unsupported`` may pass that way. Missing observations
    are represented by the adapter returning ``None`` and always fail closed.
    """

    succeeded: bool | None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.succeeded is not None and type(self.succeeded) is not bool:
            raise ValueError("egress observation succeeded must be boolean or None")
        if not isinstance(self.detail, str):
            raise ValueError("egress observation detail must be a string")
        if self.succeeded is None and not self.detail.strip():
            raise ValueError("unsupported egress observation requires detail")


class EgressResultAdapter(Protocol):
    def observation(self, probe_id: str) -> EgressProbeObservation | None:
        """Return one completed observation, or None when the probe is missing."""


@dataclass(frozen=True, slots=True)
class TaskEgressContract:
    profile: str
    expectations: tuple[EgressExpectation, ...]

    def __post_init__(self) -> None:
        if self.profile not in EGRESS_PROFILES:
            raise ValueError(f"unsupported task egress profile {self.profile!r}")
        probe_ids = tuple(item.probe_id for item in self.expectations)
        if len(probe_ids) != len(set(probe_ids)):
            raise ValueError("task egress contract contains duplicate probes")
        if not probe_ids:
            raise ValueError("task egress contract must contain probes")


@dataclass(frozen=True, slots=True)
class EgressContractCheck:
    probe_id: str
    expected_allowed: bool
    observed_succeeded: bool | None
    unsupported: bool
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EgressContractReport:
    profile: str
    checks: tuple[EgressContractCheck, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.checks)

    @property
    def failures(self) -> tuple[EgressContractCheck, ...]:
        return tuple(item for item in self.checks if not item.passed)


_DENY_PROFILE_PROBES = (
    PROBE_DENIED_PUBLIC,
    PROBE_LOOPBACK_IPV4,
    PROBE_PRIVATE_IPV4,
    PROBE_METADATA_IPV4,
    PROBE_RAW_IP_BYPASS,
    PROBE_IPV6_PUBLIC,
    PROBE_IPV6_LOOPBACK,
    PROBE_IPV6_PRIVATE,
    PROBE_IPV6_LINK_LOCAL,
    PROBE_IPV6_RAW_IP_BYPASS,
    PROBE_PROVIDER_CONTROL_CONNECTIVITY,
)
_DEPENDENCY_PROFILE_PROBES = (
    PROBE_ALLOWED_DESTINATION,
    PROBE_NON_ALLOWLISTED_DESTINATION,
    PROBE_LOOPBACK_IPV4,
    PROBE_PRIVATE_IPV4,
    PROBE_METADATA_IPV4,
    PROBE_RAW_IP_BYPASS,
    PROBE_IPV6_PUBLIC,
    PROBE_IPV6_LOOPBACK,
    PROBE_IPV6_PRIVATE,
    PROBE_IPV6_LINK_LOCAL,
    PROBE_IPV6_RAW_IP_BYPASS,
    PROBE_REDIRECT_BYPASS,
    PROBE_PROVIDER_CONTROL_CONNECTIVITY,
)


def task_egress_contract(profile: str) -> TaskEgressContract:
    """Return destination-level expectations for one hardened execution profile."""
    if profile not in EGRESS_PROFILES:
        raise ValueError(f"unsupported task egress profile {profile!r}")

    probes = (
        _DEPENDENCY_PROFILE_PROBES
        if profile == PROFILE_DEPENDENCY
        else _DENY_PROFILE_PROBES
    )
    allowed = {
        PROBE_ALLOWED_DESTINATION,
        PROBE_PROVIDER_CONTROL_CONNECTIVITY,
    }
    return TaskEgressContract(
        profile=profile,
        expectations=tuple(
            EgressExpectation(probe_id, probe_id in allowed)
            for probe_id in probes
        ),
    )


def evaluate_task_egress_contract(
    contract: TaskEgressContract,
    adapter: EgressResultAdapter,
) -> EgressContractReport:
    """Evaluate provider/runtime observations without provider-specific logic."""
    if not isinstance(contract, TaskEgressContract):
        raise TypeError("contract must be TaskEgressContract")

    checks: list[EgressContractCheck] = []
    for expectation in contract.expectations:
        spec = PROBE_SPEC_BY_ID[expectation.probe_id]
        observation = adapter.observation(expectation.probe_id)
        if observation is None:
            checks.append(
                EgressContractCheck(
                    probe_id=expectation.probe_id,
                    expected_allowed=expectation.allowed,
                    observed_succeeded=None,
                    unsupported=False,
                    passed=False,
                    detail="missing probe observation",
                )
            )
            continue
        if not isinstance(observation, EgressProbeObservation):
            raise TypeError(
                f"adapter observation for {expectation.probe_id!r} "
                "must be EgressProbeObservation or None"
            )

        if observation.succeeded is None:
            checks.append(
                EgressContractCheck(
                    probe_id=expectation.probe_id,
                    expected_allowed=expectation.allowed,
                    observed_succeeded=None,
                    unsupported=True,
                    passed=spec.optional_if_unsupported,
                    detail=observation.detail,
                )
            )
            continue

        checks.append(
            EgressContractCheck(
                probe_id=expectation.probe_id,
                expected_allowed=expectation.allowed,
                observed_succeeded=observation.succeeded,
                unsupported=False,
                passed=observation.succeeded is expectation.allowed,
                detail=observation.detail,
            )
        )

    return EgressContractReport(profile=contract.profile, checks=tuple(checks))
