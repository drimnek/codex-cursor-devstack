# Modular Package Layout

Status: **CORE, AgentDriver, and Runtime Backend phases implemented through MA2-RT-005**

This document records the implemented Python package and compatibility-entrypoint
boundary after the completed CORE extraction series.

`MA2-CORE-001` through `MA2-CORE-006` preserve the frozen v0.1 external
behavior while moving provider-neutral lifecycle and RPC responsibilities behind
package-level APIs.

The provider-neutral AgentDriver contract, trusted in-tree AgentRegistry, provider
state adapters, concrete Codex/Cursor reference drivers, and fake-driver
extensibility proof are implemented through `MA2-DRV-006`. Provider-native
authentication, version probes, state metadata, configuration reconciliation,
and run-command construction are owned by drivers rather than generic broker
operations.

The resolved execution-plan, Podman runtime-backend, streaming/process-control,
and non-agent GitNexus runtime-consumer boundaries are implemented through
`MA2-RT-005`. Provider-neutral policy resolution remains the next major
migration stage described in `docs/multi-agent-architecture-v0.2.md`.

## Current package structure

```text
platform-src/
├── agentdev/
│   ├── __init__.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── state.py
│   │   ├── codex.py
│   │   └── cursor.py
│   ├── broker/
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   ├── daemon.py
│   │   ├── rpc.py
│   │   └── runtime_io.py
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
│   │   ├── __init__.py
│   │   └── plan.py
│   ├── policy/
│   │   └── __init__.py
│   └── runtime/
│       ├── __init__.py
│       ├── base.py
│       └── podman.py
└── bin/
    ├── agentctl
    └── agentd
```

`agents` contains the provider-neutral driver/state contracts, trusted registry,
and both concrete reference drivers (`CodexDriver` and `CursorDriver`).
`execution/plan.py` now owns the resolved executor-plan model, while `runtime`
contains the provider-neutral backend contract and concrete `PodmanBackend`.
`policy` remains the major structural placeholder for the next migration phase.

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

## Implemented driver layer

```text
agentdev/agents/base.py
agentdev/agents/registry.py
agentdev/agents/state.py
agentdev/agents/codex.py
agentdev/agents/cursor.py
```

`base.py` defines immutable provider-neutral specifications for capabilities,
installation metadata, persistent state mounts, authentication, version
probing, provider-native policy artifacts, and provider run commands.

`registry.py` owns the fixed trusted set of in-tree driver objects used by the
broker. Generic provider acceptance and enumeration no longer relies on a
separate `ALLOWED_PROVIDERS` constant or transitional provider identities.

`state.py` defines provider-owned state layout, migration, native config target,
and managed-state reconciliation metadata consumed by generic broker/runtime
code.

`CodexDriver` and `CursorDriver` are the two concrete reference drivers. They
own their provider CLI authentication/status/version/run syntax and their
provider-specific state/configuration semantics. The drivers remain
declarative and do not invoke Podman or own project/task lifecycle behavior.

`MA2-DRV-006` adds no production driver. Its fake driver exists only in
`tests/fake-driver-extensibility-regression.py` and proves that a provider with
an unrelated state target, executable, version probe, authentication command,
and minimal capability set can be registered temporarily and consumed by
generic broker paths. No fake-provider identifier or command is present in the
production registry or project/task/Git/runtime modules.

## Implemented execution-plan and runtime boundary

```text
agentdev/execution/plan.py
agentdev/runtime/base.py
agentdev/runtime/podman.py
agentdev/broker/runtime_io.py
```

`execution/plan.py` defines the immutable `ResolvedExecutionPlan` consumed by the
runtime layer. Broker orchestration authorizes project-derived host paths and
resolves provider `RunSpec` data, state mounts, policy artifacts, task
environment, resources, network requirements, and interaction mode before a
runtime backend is invoked.

`runtime/base.py` defines `RuntimeBackend`, normalized runtime completion, and
input/resize/cancel control events without depending on RPC framing or provider
CLI semantics.

`runtime/podman.py` implements `PodmanBackend` and owns translation of resolved
plans into the rootless Podman executor envelope, including mounts, environment,
resource/network flags, read-only rootfs controls, PTY/process lifetime,
cancellation, and interactive container cleanup.

`broker/runtime_io.py` remains broker-owned because it maps RPC frames to the
provider-neutral runtime I/O contract. This keeps RPC JSON/base64 framing out of
the runtime implementation.

`MA2-RT-004` completes the process-control boundary: raw Podman execution selects
interactive versus noninteractive streaming inside the runtime layer, and the
runtime owns PTY setup, stdout forwarding, input, resize, cancellation, signal
escalation, cidfiles, and cleanup. Provider authentication supplies only its
driver-declared interaction requirement and timeout.

`MA2-RT-005` proves that the runtime layer is not synonymous with the agent
registry. GitNexus indexing is authorized by the broker and executed
noninteractively through the runtime boundary, but GitNexus is neither an
`AgentDriver` nor a production registry entry. Failure to build its optional
intelligence image remains non-fatal for core executors.

GitNexus keeps two persistent state scopes without widening the executor trust
boundary:

```text
task workspace/.gitnexus/                  repository-local index
<project>/runtime/gitnexus/<task>/         per-task GitNexus runtime home
  .gitnexus/registry.json                  persistent repository registry
  .lbdb/                                   LadybugDB runtime/extension state
```

The per-task runtime home is mounted only at `/gitnexus-home:rw` and supplied as
`HOME=/gitnexus-home`; the container root remains read-only and no writable whole
`/root` is introduced. When the per-task registry is absent, indexing performs a
one-time forced rebuild so a previously persisted repository index cannot remain
paired with a missing registry. Subsequent runs retain normal incremental
analysis behavior.

GitNexus indexing continues to run with task networking disabled. The current
intelligence image does not pre-seed the optional LadybugDB FTS extension, so an
offline index run may report `FTS extension unavailable; continuing without FTS
features`. This is a known degraded optional capability, not an RT-005 failure:
the repository index can still finalize successfully without FTS.

TODO (optional GitNexus enhancement): during intelligence-image/bootstrap
preparation, pre-install a version-compatible LadybugDB FTS extension and seed it
into new per-task GitNexus homes, then use a load-only/offline runtime mode. This
must remain optional and non-fatal, must not grant `agentctl index` task-network
access, and must not turn GitNexus into an `AgentDriver`.

The agent-run path is now:

```text
AgentDriver -> RunSpec
             |
             v
broker authorization -> ResolvedExecutionPlan
                         |
                         v
                    PodmanBackend
```

## Responsibilities still in the broker daemon

After runtime-backend extraction, `agentdev/broker/daemon.py` continues to own or
orchestrate control-plane concerns including:

```text
project/task path authorization and ResolvedExecutionPlan construction
provider-state migration execution from driver metadata
provider auth/status/version orchestration and RPC framing
image build, version, and smoke orchestration
GitNexus task authorization and non-agent runtime request construction
other control-plane Podman maintenance operations
```

Agent-run Podman argv translation, resolved mount/environment/resource/network
materialization, PTY/process lifetime, cancellation, and interactive container
cleanup are owned by `PodmanBackend`. RPC input/output framing remains in the
broker adapter rather than the runtime backend.

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
tests/agent-driver-contract-regression.py
tests/agent-registry-regression.py
tests/codex-driver-regression.py
tests/cursor-driver-regression.py
tests/provider-state-driver-regression.py
tests/fake-driver-extensibility-regression.py
tests/resolved-execution-plan-regression.py
tests/runtime-backend-contract-regression.py
tests/podman-backend-regression.py
tests/runtime-streaming-boundary-regression.py
tests/gitnexus-runtime-consumer-regression.py
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

The completed CORE, AgentDriver, and Runtime Backend phases through `MA2-RT-005`
provide the provider-neutral domain, provider, execution-plan, streaming, and
runtime foundations while preserving GitNexus as a non-agent runtime consumer.

The next architecture boundary is the provider-neutral policy model:

```text
ExecutionPolicy
PolicyResolver
ExecutionProfile
SecurityClass
CapabilityRequirement
```

The next-stage rule is:

1. keep `ResolvedExecutionPlan` as the only input to concrete agent-run runtime
   execution;
2. resolve security semantics in broker-owned policy code before Podman starts;
3. translate provider-neutral policy into provider-native artifacts only through
   trusted drivers;
4. preserve the frozen provider invocation and executor-boundary contracts until
   a policy case explicitly changes them;
5. do not mark credential isolation or destination-level egress as hardened until
   observable enforcement and adversarial acceptance tests exist.
