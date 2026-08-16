#!/usr/bin/env python3
"""Verify that the deterministic package baseline has no orphaned regressions."""

from __future__ import annotations

import sys
from pathlib import Path


TESTS = Path(__file__).resolve().parent
PACKAGE_RUNNER = TESTS / "package-check.sh"

DETERMINISTIC_PATTERNS = (
    "*-regression.py",
    "*-source-audit.py",
)
DETERMINISTIC_EXPLICIT = {
    "git-model-smoke.sh",
}
RUNTIME_E2E_PATTERN = "run-*-e2e.sh"


def referenced_by_package_runner(name: str, source: str) -> bool:
    return f'tests/{name}' in source


def fail(message: str) -> int:
    print(f"package baseline regression failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    source = PACKAGE_RUNNER.read_text(encoding="utf-8")

    deterministic = set(DETERMINISTIC_EXPLICIT)
    for pattern in DETERMINISTIC_PATTERNS:
        deterministic.update(path.name for path in TESTS.glob(pattern))

    # This regression is itself part of the deterministic package baseline.
    deterministic.add(Path(__file__).name)

    missing = sorted(
        name
        for name in deterministic
        if not referenced_by_package_runner(name, source)
    )
    if missing:
        return fail(
            "deterministic tests are not executed by tests/package-check.sh: "
            + ", ".join(missing)
        )

    runtime_e2e = sorted(path.name for path in TESTS.glob(RUNTIME_E2E_PATTERN))
    overlap = sorted(set(runtime_e2e) & deterministic)
    if overlap:
        return fail(
            "runtime E2E entry points must not be classified as deterministic "
            "regressions: " + ", ".join(overlap)
        )

    print("deterministic package baseline inventory checks passed")
    if runtime_e2e:
        print("runtime E2E entry points: " + ", ".join(runtime_e2e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
