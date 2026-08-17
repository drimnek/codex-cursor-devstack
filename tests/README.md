# Test Baseline

`./tests/package-check.sh` is the authoritative deterministic package acceptance
command for the repository.

It is the T3 gate used before implementation changes are considered complete.
The command must remain runnable without provider credentials or an already
deployed platform. Tests that require a deployed or authenticated environment
belong to runtime E2E validation instead.

## Deterministic package baseline

`tests/package-baseline-regression.py` inventories the top-level test entry
points that are expected to participate in package acceptance.

The following naming rules are part of the baseline contract:

- `*-regression.py` — deterministic regression test;
- `*-source-audit.py` — deterministic source-level boundary audit;
- `git-model-smoke.sh` — explicit deterministic Git-model smoke test;
- `run-*-e2e.sh` — runtime E2E entry point, not classified as a deterministic
  regression solely by its filename.

Every deterministic test discovered by those rules must be referenced by
`tests/package-check.sh`. Adding a new deterministic regression without wiring
it into the package runner therefore fails package acceptance instead of
silently orphaning the test.

A runtime E2E entry point may contain deterministic preflight, harness, or skip
behavior, but an authenticated/deployed execution path is not part of the T3
contract merely because the script is present or invoked for such a preflight.

## Runtime E2E

Runtime E2E validation is separate from deterministic package acceptance. It
may require some combination of:

- the deployed broker and rootless Podman runtime;
- built provider images;
- provider authentication;
- provider network access;
- a disposable imported project.

These checks should remain explicitly opt-in or environment-gated when they
would otherwise make `./tests/package-check.sh` depend on external state.

For v0.2 work, run the deterministic gate first:

```bash
./tests/package-check.sh
```

Then run the runtime/E2E acceptance required by the specific backlog item.

The frozen v0.1 lifecycle baseline is:

```bash
AGENTDEV_RUN_LIFECYCLE_E2E=1 \
  tests/run-lifecycle-e2e.sh
```

On a host where Codex nested sandbox initialization fails because user namespaces
cannot be created inside the rootless executor, use the explicit compatibility
mode validated for that environment:

```bash
AGENTDEV_RUN_LIFECYCLE_E2E=1 \
AGENTDEV_CODEX_OUTER_ONLY=1 \
  tests/run-lifecycle-e2e.sh
```

Security baseline validation has two layers:

- `tests/executor-boundary-source-audit.py` checks the source-level executor
  boundary and reports `PASS`, `WARN`, and `FAIL` findings;
- `tests/executor-security-baseline.py` freezes the currently closed guarantees
  while intentionally retaining provider credential readability and
  destination-level outbound egress as open findings.

An accepted security baseline requires zero `FAIL` findings. Existing `WARN`
findings must not be silently reclassified as hardened guarantees.
