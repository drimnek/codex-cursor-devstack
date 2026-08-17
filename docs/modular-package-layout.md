# Modular Package Layout

Status: **Implemented through MA2-CORE-006**

This document records the implemented Python package and compatibility-entrypoint
boundary after the completed CORE extraction series.

`MA2-CORE-001` through `MA2-CORE-006` preserve the frozen v0.1 external
behavior while moving provider-neutral lifecycle and RPC responsibilities behind
package-level APIs.

The provider-neutral AgentDriver contract and trusted in-tree AgentRegistry
are now implemented by `MA2-DRV-001` and `MA2-DRV-002`. Concrete Codex/Cursor
semantics are not yet extracted: provider state, authentication, command,
policy, and runtime behavior still remains in the broker until the following
driver/runtime cases.

The runtime-backend and execution-policy layers remain later migration stages
described in `docs/multi-agent-architecture-v0.2.md`.

## Current package structure

```text
platform-src/
├── agentdev/
│   ├── __init__.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── registry.py
│   ├── broker/
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   ├── daemon.py
│   │   └── rpc.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── validation.py
│   │   ├── projects.py
│   │   ├── git_handoff.py
│   │   ├── tasks.py
│   │   ├── dependencies.py
│   │   ├── worktrees.py
│   │   └── locking.py
│   ├── execution/
│   │   └── __init__.py
│   ├── policy/
│   │   └── __init__.py
│   └── runtime/
│       └── __init__.py
└── bin/
    ├── agentctl
    └── agentd
```

`agents` now contains the implemented provider-neutral driver contract and the
trusted registry. It does not yet contain concrete Codex/Cursor drivers.
`execution`, `policy`, and `runtime` remain structural package boundaries for
subsequent migration phases and should not yet be interpreted as implemented
execution-plan, policy-engine, or runtime-backend subsystems.

## Public entrypoint boundary

The public executable entrypoints remain:

```text
platform-src/bin/agentctl
platform-src/bin/agentd
```

They are compatibility loaders rather than authoritative implementation files.

The current packaged implementation boundaries are:

```text
controller implementation
    -> platform-src/agentdev/broker/cli.py

broker operation/orchestration implementation
    -> platform-src/agentdev/broker/daemon.py

generic broker RPC boundary
    -> platform-src/agentdev/broker/rpc.py
```

Runtime/compatibility tests may continue to execute or load the public
entrypoints. Source-level semantic checks and audits should inspect the package
module that owns the behavior being tested.

The compatibility loaders preserve the frozen v0.1 regression surface during
the migration, including tests that monkeypatch entrypoint module globals.

## Implemented CORE responsibilities

### Shared models and validation

```text
agentdev/core/models.py
agentdev/core/validation.py
```

These modules own provider-neutral project/task context structures and shared
identifier, Git-branch, and canonical-path validation.

Compatibility exports required by the frozen v0.1 tests remain available
through the broker entrypoint where necessary.

### Project and Git handoff

```text
agentdev/core/projects.py
agentdev/core/git_handoff.py
```

These modules own provider-neutral project path resolution, repository
initialization/synchronization, Git bundle validation and handoff, integration
export, and project Git status.

The human/agent Git trust boundary is unchanged: human-side controller code
creates and consumes bundles but does not directly operate the agent-owned
repository.

### Task, dependency, and worktree lifecycle

```text
agentdev/core/tasks.py
agentdev/core/dependencies.py
agentdev/core/worktrees.py
```

These modules own task metadata and state transitions, dependency validation,
parallel branch/worktree preparation, recorded-head validation, merge, and
abort mechanics.

Provider execution remains outside these modules.

### Locking

```text
agentdev/core/locking.py
```

The locking service owns lock paths, shared/exclusive `flock` acquisition, and
provider-neutral lock-selection helpers.

The established lock semantics remain unchanged, including
integration-before-task acquisition order for merge and abort flows.

### Broker RPC

```text
agentdev/broker/rpc.py
```

The RPC module owns request decoding, request-shape validation, public operation
dispatch, peer metadata, response/error framing, start/exit/output framing, and
fd-3 socket serving.

`daemon.py` supplies concrete operation callables per request. This preserves
the frozen characterization-test behavior in which operation globals can be
monkeypatched through the compatibility entrypoint.

## Implemented driver contract and trusted registry

```text
agentdev/agents/base.py
agentdev/agents/registry.py
```

`base.py` defines immutable provider-neutral specifications for capabilities,
installation metadata, persistent state mounts, authentication, version
probing, provider-native policy artifacts, and provider run commands.

`registry.py` owns the fixed trusted set of in-tree driver objects used by the
broker. Generic provider acceptance and enumeration no longer relies on a
separate `ALLOWED_PROVIDERS` constant.

At this checkpoint, the built-in Codex/Cursor registrations are deliberately
transitional identity bridges. They fail closed for provider semantics that
have not yet been migrated. This prevents the registry from duplicating or
inventing provider behavior while the extraction is incomplete.

## Responsibilities still in the broker daemon

The CORE extraction intentionally does not move provider/runtime concerns yet.

`agentdev/broker/daemon.py` still owns or orchestrates areas including:

```text
provider selection
Codex/Cursor state and authentication behavior
provider command construction
provider version/status behavior
executor Podman invocation
PTY and interactive executor streaming
provider-specific sandbox compatibility
provider/runtime smoke operations
```

These are the main inputs to the upcoming AgentDriver and runtime-backend
extraction phases.

## Frozen behavioral contracts

The CORE series is structural. The following remain frozen across
`MA2-CORE-001` through `MA2-CORE-006`:

- public `agentctl` command-line behavior;
- broker RPC operation and request-field compatibility;
- provider invocation commands and executor envelope;
- sequential and parallel task lifecycle semantics;
- dependency and recorded-head semantics;
- human/agent Git trust boundary;
- lock modes and lock ordering;
- deterministic package acceptance;
- currently closed executor-security guarantees;
- explicit credential-confidentiality and destination-egress open findings.

A change in these contracts is not implied by the CORE modularization.

## Testing implications

The authoritative deterministic T3 acceptance command remains:

```bash
./tests/package-check.sh
```

CORE-specific deterministic regressions now include:

```text
tests/modular-package-layout-regression.py
tests/core-validation-regression.py
tests/project-git-handoff-regression.py
tests/task-lifecycle-core-regression.py
tests/locking-service-regression.py
tests/broker-rpc-server-regression.py
```

They complement the frozen characterization and security tests, including:

```text
tests/security-regression.py
tests/broker-rpc-contract-regression.py
tests/provider-invocation-regression.py
tests/dependency-semantics-regression.py
tests/parallel-lifecycle-regression.py
tests/locking-concurrency-regression.py
tests/executor-security-baseline.py
```

Source-level tests must follow implementation ownership rather than assuming
that the thin `platform-src/bin/agentctl` or `platform-src/bin/agentd` files
contain the full implementation.

The executor security audit is expected to remain behaviorally stable during
this structural stage. A change in its PASS/WARN/FAIL findings should be
reviewed rather than accepted as an incidental refactor result.

## Deployment requirement

Deployment must install the complete `platform-src/agentdev` package together
with the thin entrypoints.

A post-bootstrap deployment should contain, at minimum:

```text
/srv/agent-dev/platform/agentdev/broker/cli.py
/srv/agent-dev/platform/agentdev/broker/daemon.py
/srv/agent-dev/platform/agentdev/broker/rpc.py
/srv/agent-dev/platform/agentdev/core/
```

and normal runtime checks must continue to succeed:

```bash
agentctl ping
agentctl versions
agentctl smoke
agentctl status
```

The public CLI must not require an RPC protocol change to communicate with the
refactored broker.

## Next extraction boundary

The completed CORE series provides the provider-neutral domain and RPC
foundation for:

```text
MA2-DRV-001
    AgentCapabilities
    RunSpec
    AgentDriver
```

The next-stage extraction rule is:

1. keep project/task/Git/locking behavior provider-neutral;
2. move provider-specific state, authentication, version, command, and sandbox
   semantics behind the driver contract;
3. preserve the frozen provider invocation contract unless a backlog item
   explicitly changes it;
4. keep Podman/runtime execution separate from provider-driver semantics;
5. do not promote current credential or network warnings into hardened claims
   without observable enforcement and acceptance tests.
