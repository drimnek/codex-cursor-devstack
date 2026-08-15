#!/usr/bin/env python3
"""Regression checks for the package-level provider E2E runner."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "run-cross-provider-parallel-e2e.sh"


def run_runner(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in (
        "AGENT_E2E_PROJECT",
        "AGENT_E2E_RUN_ID",
        "AGENT_E2E_REQUIRED",
        "AGENT_E2E_SCRIPT",
    ):
        merged.pop(key, None)
    merged.update(env)
    return subprocess.run(
        [str(RUNNER)],
        cwd=ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_contains(haystack: str, needle: str) -> None:
    assert needle in haystack, (needle, haystack)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = tmp_path / "args.log"
        stub = tmp_path / "provider-e2e-stub.sh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"printf '%s\\n' \"$@\" > {log!s}\n"
            "exit \"${STUB_RC:-0}\"\n"
        )
        stub.chmod(0o755)

        result = run_runner({"AGENT_E2E_SCRIPT": str(stub)})
        assert result.returncode == 0, result
        assert_contains(result.stdout, "cross-provider parallel E2E: SKIP")
        assert not log.exists()

        result = run_runner(
            {
                "AGENT_E2E_SCRIPT": str(stub),
                "AGENT_E2E_REQUIRED": "1",
            }
        )
        assert result.returncode == 1, result
        assert_contains(result.stderr, "cross-provider parallel E2E: FAIL")
        assert not log.exists()

        result = run_runner(
            {
                "AGENT_E2E_SCRIPT": str(stub),
                "AGENT_E2E_PROJECT": "e2e-handoff",
            }
        )
        assert result.returncode == 0, result
        assert_contains(result.stdout, "cross-provider parallel E2E: RUN project=e2e-handoff")
        assert_contains(result.stdout, "cross-provider parallel E2E: PASS")
        assert log.read_text().splitlines() == ["e2e-handoff"]

        result = run_runner(
            {
                "AGENT_E2E_SCRIPT": str(stub),
                "AGENT_E2E_PROJECT": "e2e-handoff",
                "AGENT_E2E_RUN_ID": "package-check",
            }
        )
        assert result.returncode == 0, result
        assert log.read_text().splitlines() == ["e2e-handoff", "package-check"]

        result = run_runner(
            {
                "AGENT_E2E_SCRIPT": str(stub),
                "AGENT_E2E_PROJECT": "e2e-handoff",
                "STUB_RC": "7",
            }
        )
        assert result.returncode == 7, result
        assert_contains(result.stderr, "cross-provider parallel E2E: FAIL (rc=7)")

        result = run_runner(
            {
                "AGENT_E2E_SCRIPT": str(stub),
                "AGENT_E2E_REQUIRED": "yes",
            }
        )
        assert result.returncode == 2, result
        assert_contains(result.stderr, "AGENT_E2E_REQUIRED must be 0 or 1")

    print("provider E2E runner regression checks passed")


if __name__ == "__main__":
    main()
