#!/usr/bin/env python3
"""Regression coverage for MA2-POL-005 legacy run flag/profile mapping."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.policy.legacy import (  # noqa: E402
    LegacyProfileConflictError,
    RunProfileMapping,
    resolve_run_profile_request,
)


def expect(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def check_legacy_matrix() -> None:
    assert resolve_run_profile_request() == RunProfileMapping(
        "implement", False, False, "compatibility"
    )
    assert resolve_run_profile_request(readonly=False, outer_only=False) == RunProfileMapping(
        "implement", False, False, "compatibility"
    )
    assert resolve_run_profile_request(readonly=True, outer_only=False) == RunProfileMapping(
        "review", True, False, "compatibility"
    )
    assert resolve_run_profile_request(readonly=False, outer_only=True) == RunProfileMapping(
        "implement", False, True, "compatibility"
    )
    assert resolve_run_profile_request(readonly=True, outer_only=True) == RunProfileMapping(
        "review", True, True, "compatibility"
    )


def check_direct_profile_mapping() -> None:
    assert resolve_run_profile_request(profile="review") == RunProfileMapping(
        "review", True, False, None
    )
    assert resolve_run_profile_request(profile="implement") == RunProfileMapping(
        "implement", False, False, None
    )
    assert resolve_run_profile_request(profile="dependency") == RunProfileMapping(
        "dependency", False, False, None
    )
    assert resolve_run_profile_request(profile="compatibility") == RunProfileMapping(
        "compatibility", False, False, "compatibility"
    )

    # Equivalent aliases are deterministic rather than treated as conflicts.
    assert resolve_run_profile_request(profile="review", readonly=True) == RunProfileMapping(
        "review", True, False, None
    )
    assert resolve_run_profile_request(profile="implement", readonly=False) == RunProfileMapping(
        "implement", False, False, None
    )

    # outer_only is an orthogonal compatibility modifier and may coexist with
    # operational review/implement intent.
    assert resolve_run_profile_request(
        profile="review", readonly=True, outer_only=True
    ) == RunProfileMapping("review", True, True, "compatibility")
    assert resolve_run_profile_request(
        profile="compatibility", readonly=True
    ) == RunProfileMapping("compatibility", True, False, "compatibility")


def check_conflicts_and_validation() -> None:
    expect(
        LegacyProfileConflictError,
        resolve_run_profile_request,
        profile="review",
        readonly=False,
    )
    expect(
        LegacyProfileConflictError,
        resolve_run_profile_request,
        profile="implement",
        readonly=True,
    )
    expect(
        LegacyProfileConflictError,
        resolve_run_profile_request,
        profile="dependency",
        readonly=True,
    )
    expect(ValueError, resolve_run_profile_request, profile="unknown")
    expect(TypeError, resolve_run_profile_request, profile=1)
    expect(TypeError, resolve_run_profile_request, readonly=1)
    expect(TypeError, resolve_run_profile_request, outer_only="yes")


def check_broker_and_public_surface() -> None:
    daemon = (PLATFORM_SRC / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    op_start = daemon.index("def op_run")
    op_end = daemon.index("def rpc_operations", op_start)
    op_run = daemon[op_start:op_end]
    assert "resolve_run_profile_request(" in op_run
    assert "legacy_mapping.readonly" in op_run
    assert "legacy_mapping.outer_only" in op_run
    assert "legacy_mapping.security_class" in op_run
    assert "security_class=security_class" in op_run
    assert '"security_class": plan.security_class' in op_run

    # POL-005 intentionally does not expose the new profile CLI/RPC yet.
    rpc_contract = (ROOT / "tests/broker-rpc-contract-regression.py").read_text(
        encoding="utf-8"
    )
    assert '"run": {"op", "provider", "project", "task", "readonly", "outer_only", "prompt"}' in rpc_contract
    assert '"readonly": False' in rpc_contract
    assert '"outer_only": False' in rpc_contract


def main() -> None:
    check_legacy_matrix()
    check_direct_profile_mapping()
    check_conflicts_and_validation()
    check_broker_and_public_surface()
    print("legacy run profile mapping regression checks passed")


if __name__ == "__main__":
    main()
