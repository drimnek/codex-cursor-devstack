# Pre-CORE Baseline Checkpoint

Status: **Frozen pre-refactor baseline — preserved through MA2-CORE-006**

This document records the implementation state that had to remain behaviorally
stable while the v0.2 CORE modularization was performed. It remains the
historical pre-refactor checkpoint and is not a replacement for
`docs/multi-agent-architecture-v0.2.md`, `docs/modular-package-layout.md`, or
`docs/executor-boundary-audit.md`.

`MA2-CORE-001` through `MA2-CORE-006` completed without intentionally changing
this baseline. Current package/module ownership is documented in
`docs/modular-package-layout.md`.

## Completed baseline work

The pre-CORE baseline now covers:

- Cursor immutable installer-layout regression alignment;
- deterministic package-test inventory and authoritative T3 acceptance;
- broker RPC compatibility characterization and explicit request/internal-error
  separation;
- provider invocation characterization for Codex and Cursor;
- opt-in v0.1 lifecycle E2E acceptance across sequential and parallel task
  flows;
- executor security baseline regression checks;
- persistent Cursor XDG authentication state under a scoped provider volume.

These checks were established to make the package/module extraction
mechanical: moving code must not silently change the public RPC surface,
provider commands, Git/task lifecycle behavior, or already-closed executor
boundaries.

## Current provider state layout

The persistent provider state contract before CORE extraction is:

```text
agent-dev-codex-state   -> /root/.codex
agent-dev-cursor-state  -> /root/.cursor
agent-dev-cursor-auth   -> /root/.config/cursor
```

The Cursor authentication volume exists because the CLI stores Linux/XDG
authentication state separately from `~/.cursor/cli-config.json`.

The scoped volume layout avoids a writable persistent `/root`, but it does not
make provider credentials confidential from processes already running inside
the corresponding executor.

## Validation layers

### Deterministic package acceptance

The authoritative T3 gate is:

```bash
./tests/package-check.sh
```

It must remain runnable without provider credentials and without an already
deployed platform.

### Runtime lifecycle acceptance

The v0.1 lifecycle E2E runner is explicitly opt-in:

```bash
AGENTDEV_RUN_LIFECYCLE_E2E=1 \
  tests/run-lifecycle-e2e.sh
```

The currently validated rootless Podman host requires explicit Codex
compatibility mode because the nested Codex Linux sandbox cannot create its
user namespace inside the executor:

```bash
AGENTDEV_RUN_LIFECYCLE_E2E=1 \
AGENTDEV_CODEX_OUTER_ONLY=1 \
  tests/run-lifecycle-e2e.sh
```

This is an environment-specific compatibility requirement. `--outer-only`
remains weaker than a successfully enforced nested provider sandbox.

### Security baseline

The baseline distinguishes closed guarantees from open hardening work.

Currently frozen closed guarantees include:

- read-only executor root filesystem;
- dropped Linux capabilities and `no-new-privileges`;
- constrained workspace/reference/task metadata mount modes;
- no persistent whole-provider-home mount at `/root`;
- no raw Podman/Docker socket exposure;
- explicit provider/non-provider network modes;
- no implicit proxy inheritance into executors;
- resource limits and controlled tmpfs mounts;
- human/agent Git trust-boundary enforcement.

Currently open findings include:

- provider credential confidentiality from task processes;
- destination-level outbound egress restriction.

Those open findings must remain explicit `WARN`/limitation-level statements
until later security requirements close them with observable enforcement and
adversarial acceptance tests.

## CORE transition outcome

`MA2-CORE-001` through `MA2-CORE-006` preserved this baseline while extracting
provider-neutral validation, project/Git lifecycle, task/dependency/worktree
lifecycle, locking, and RPC responsibilities into package-level modules.

This document should now remain stable as the historical pre-refactor
reference. Current implementation ownership is documented in
`docs/modular-package-layout.md`.

Subsequent AgentDriver, runtime, and policy work must continue to preserve the
relevant frozen contracts unless a backlog item explicitly changes them.
