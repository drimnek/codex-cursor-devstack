"""Canonical serialization and hashing for resolved execution policy.

The canonical representation contains only the normalized provider-neutral
``ExecutionPolicy`` value. Runtime identity, provider-generated artifacts,
image IDs, paths, timestamps, and other transient execution values are not part
of the policy fingerprint.
"""
from __future__ import annotations

import hashlib
import json

from agentdev.policy.schema import ExecutionPolicy

POLICY_HASH_ALGORITHM = "sha256"
POLICY_HASH_PREFIX = f"{POLICY_HASH_ALGORITHM}:"


def _require_policy(policy: object) -> ExecutionPolicy:
    if not isinstance(policy, ExecutionPolicy):
        raise TypeError("policy must be ExecutionPolicy")
    return policy


def canonical_policy_json(policy: ExecutionPolicy) -> str:
    """Return the stable compact JSON representation of a resolved policy."""
    resolved = _require_policy(policy)
    return json.dumps(
        resolved.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_policy_bytes(policy: ExecutionPolicy) -> bytes:
    """Return UTF-8 bytes used as the policy hash input."""
    return canonical_policy_json(policy).encode("utf-8")


def policy_hash(policy: ExecutionPolicy) -> str:
    """Return a self-describing SHA-256 fingerprint for a resolved policy."""
    digest = hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()
    return f"{POLICY_HASH_PREFIX}{digest}"
