# Multi-Agent v0.2 Implementation Backlog

Status: **Active**

Target: **v0.2**

Architecture source: [`multi-agent-architecture-v0.2.md`](multi-agent-architecture-v0.2.md)

Security baseline: [`executor-boundary-audit.md`](executor-boundary-audit.md)

Current implementation baseline: **v0.1.x**

---

## 1. Purpose

This document converts the v0.2 multi-agent architecture into an implementation
backlog suitable for requirement-driven development and testing.

It is intentionally more implementation-oriented than
`multi-agent-architecture-v0.2.md`.

The backlog defines:

- implementation order;
- requirement boundaries;
- dependencies;
- implementation constraints;
- expected source changes;
- unit/regression/acceptance/security tests;
- phase exit gates;
- definition of done.

The backlog does not replace the architecture document. If a backlog item and
the architecture document conflict, the architecture document is authoritative
until the design is explicitly amended.

---

## 2. Global Implementation Instructions

These instructions apply to every backlog item unless the item explicitly says
otherwise.

### 2.1 Requirement-sized changes

Each backlog requirement should be independently reviewable and should normally
produce one focused commit.

Do not combine unrelated architectural migrations in one requirement.

A requirement may introduce preparatory interfaces without migrating all
callers, but the repository must remain runnable and testable after every
completed requirement.

### 2.2 Test-first workflow

Use regression-first / TDD-style implementation for behavior that can be
expressed before the implementation change.

Preferred sequence:

```text
1. identify the observable contract;
2. add or update a failing regression test;
3. implement the smallest source change that satisfies the contract;
4. run focused tests;
5. run ./tests/package-check.sh;
6. update documentation if externally visible behavior changed;
7. commit the completed requirement.
```

Pure mechanical moves may begin with characterization tests instead of a new
failing test, but they must preserve existing behavior.

### 2.3 Preserve existing trust boundaries

No v0.2 requirement may weaken the existing guarantees around:

```text
human repo / agent repo separation
Git bundle handoff
broker-controlled mounts
raw Podman socket isolation
project path validation
task/worktree lifecycle
dependency semantics
locking
resource limits
```

A refactor is incomplete if those guarantees are only assumed rather than
covered by regression tests.

### 2.4 Keep provider logic out of the domain core

Generic modules must not branch on provider identity.

Disallowed direction:

```python
if provider == "codex":
    ...
elif provider == "cursor":
    ...
```

inside:

```text
core/
runtime/
policy resolver/
generic broker RPC/
generic test contracts/
```

Provider-specific branching is allowed inside trusted provider drivers and
their provider-specific policy adapters.

### 2.5 Fail closed for security capabilities

If a hardened execution profile requires a capability that a provider cannot
prove, the broker must reject the run.

It must not:

- silently disable a provider-native sandbox;
- silently fall back to compatibility mode;
- silently widen task-shell network access;
- silently expose provider credentials to spawned commands.

### 2.6 Provider configuration is generated enforcement material

Codex, Cursor, Copilot, and Antigravity configuration formats are not the
platform policy model.

Tests should primarily assert platform behavior and resolved policy contracts,
not provider configuration strings.

Provider-specific tests may assert exact native configuration where required to
prove adapter correctness.

### 2.7 No arbitrary plugin loading

All agent drivers are trusted, source-controlled, in-tree components deployed
with the platform.

Do not add arbitrary user-installed Python plugins or runtime code loading to
`agentd`.

### 2.8 No manual repair in acceptance tests

A successful acceptance path must not require:

- manual provider-container execution;
- manual filesystem permission repair;
- manual provider configuration edits;
- direct Podman socket access;
- direct human-side Git operations in `repo/agent`.

### 2.9 Backward compatibility

Until the cleanup phase:

- existing Codex/Cursor commands must remain usable;
- existing RPC clients must continue to work unless a backlog item explicitly
  introduces a versioned migration;
- `--readonly` and `--outer-only` may be retained as legacy aliases;
- existing project/task metadata must remain readable.

### 2.10 Documentation discipline

When a requirement changes externally observable behavior, update the relevant
documentation in the same requirement.

Do not update `VERSION` merely because internal architecture changed. Version
changes belong to release/pilot requirements.

---

## 3. Test Levels

Backlog items refer to the following test levels.

### T0 — Static and syntax checks

Examples:

```text
python compilation
bash -n
JSON parsing
source-layout assertions
import-boundary assertions
```

These run as part of `./tests/package-check.sh`.

### T1 — Unit tests

Tests for pure models, validation, policy resolution, capability matching, and
serialization.

They must not require Podman or provider authentication.

### T2 — Broker regression tests

Tests that import broker modules or use stubs/fakes to validate:

```text
RPC validation
Podman argument construction
provider driver output
mount selection
policy translation
state layout
error handling
```

They must not make external provider API calls.

### T3 — Package acceptance

The complete deterministic package suite:

```bash
./tests/package-check.sh
```

This is mandatory after every completed implementation requirement unless the
requirement explicitly changes the runner itself and documents the temporary
red/green transition.

### T4 — Ubuntu runtime integration

Tests on the supported Ubuntu deployment using real rootless Podman but without
requiring model execution where possible.

Typical commands:

```bash
./bootstrap.sh --check
agentctl ping
agentctl build
agentctl versions
agentctl smoke
agentctl status
```

### T5 — Authenticated provider E2E

Real provider execution using authenticated provider state.

Examples:

```text
sequential implementation
cross-provider review
parallel/worktree execution
provider-specific hardened profile
```

These tests may be manually triggered or environment-gated and must not make
the deterministic package suite dependent on external provider availability.

### T6 — Adversarial security acceptance

Runtime tests intentionally attempting to violate the execution contract.

Examples:

```text
read provider credentials
read host credentials
write outside workspace
connect to denied destination
connect to private/loopback/metadata endpoints
bypass destination policy using raw IP
obtain Podman/Docker socket
```

A provider may advertise hardened support only if its required T6 contract
passes.

---

## 4. Definition of Ready

A requirement is ready for implementation when:

- its dependency requirements are complete;
- its expected externally observable behavior is clear;
- affected interfaces are identified;
- required test level is known;
- unresolved provider behavior is either verified against current vendor
  documentation or represented as an explicit capability limitation;
- no architecture decision is being invented implicitly inside the
  implementation task.

---

## 5. Definition of Done

A requirement is complete only when:

- implementation is source-controlled;
- its specified tests pass;
- `./tests/package-check.sh` passes;
- no existing closed lifecycle/security regression is broken;
- provider-specific code remains behind the intended boundary;
- new public behavior has documentation;
- no temporary debug bypass remains;
- no test requires manual post-run cleanup or filesystem repair;
- the change is committed with a focused commit message.

For T4-T6 requirements, deterministic package tests are necessary but not
sufficient; the required runtime acceptance must also be recorded.

---

# EPIC 0 — Baseline Freeze

Goal: establish a trustworthy v0.1 baseline before structural refactoring.

No architecture migration begins until this epic is complete.

---

## MA2-BL-001 — Repair Current Cursor Installer Layout Regression
### Status: DONE

Priority: **P0**

Dependencies: none.

### Objective

Make `tests/package-check.sh` reflect the current supported Cursor installation
layout.

The current Containerfile installs Cursor directly with
`HOME=/opt/cursor-cli`, while the package runner still checks for the older
copy/relocation implementation.

### Implementation

Update the package regression to verify the actual invariant:

```text
installer runs with HOME=/opt/cursor-cli
/opt/cursor-cli/.local/bin/agent exists and is executable
/usr/local/bin/agent points to that installation
the old root-home relocation pattern is not reintroduced
```

Do not weaken the regression to a generic "agent exists" test.

### Tests

- T0: Containerfile source assertions.
- T3: `./tests/package-check.sh`.

### Acceptance

- the current valid `Containerfile.cursor` passes;
- the old single-launcher-copy implementation fails;
- installation remains immutable in the image.

---

## MA2-BL-002 — Freeze Current Deterministic Package Baseline
### Status: DONE

Priority: **P0**

Dependencies: MA2-BL-001.

### Objective

Ensure every existing deterministic regression is executed by one authoritative
package runner.

### Implementation

Inventory `tests/` and verify that all deterministic regression tests intended
for package acceptance are called by `tests/package-check.sh`.

Classify tests that require a deployed/authenticated environment as optional
runtime E2E rather than silently omitting them.

### Tests

- T0: package runner source check covering the expected deterministic test set.
- T3: complete package run.

### Acceptance

The repository has one documented deterministic package acceptance command and
no known deterministic regression test is orphaned.

---

## MA2-BL-003 — Freeze Broker RPC Compatibility Contract
### Status: DONE

Priority: **P0**

Dependencies: MA2-BL-002.

### Objective

Capture the v0.1 RPC surface before moving broker code into modules.

### Implementation

Add characterization tests for:

```text
allowed operations
required/optional fields
unknown-field rejection
provider validation
run request defaults
readonly behavior
outer_only validation
error framing
interactive start/exit framing
```

Record the current request schema as a regression fixture or explicit expected
mapping.

### Tests

- T1/T2: RPC request validation and response framing.
- T3.

### Acceptance

Mechanical refactoring can change internal modules without accidentally
changing the v0.1 RPC contract.

---

## MA2-BL-004 — Freeze Current Provider Invocation Semantics
### Status: DONE

Priority: **P0**

Dependencies: MA2-BL-002.

### Objective

Characterize existing Codex/Cursor build, auth, status, version, smoke, and run
behavior before extracting drivers.

### Implementation

Add stubbed Podman/process tests covering:

```text
Codex auth command
Cursor auth command
Codex status command
Cursor status command
version probes
read-only Codex run
writable Codex run
outer-only Codex run
Cursor run
provider-state mounts
provider policy mounts
resource limits
task metadata environment
```

### Tests

- T2: exact `RunSpec`-equivalent current command characterization.
- T3.

### Acceptance

Provider extraction can be verified against the pre-refactor behavior.

---

## MA2-BL-005 — Freeze v0.1 Lifecycle E2E Baseline
### Status: DONE

Priority: **P0**

Dependencies: MA2-BL-002.

### Objective

Record the current real deployment acceptance flow before architectural
migration.

### Implementation

Document and execute, on a disposable project:

```text
project-import
sequential task implementation
cross-provider review
task completion
parallel task start
parallel execution
parallel completion
merge
abort
project-export
```

Capture expected Git heads and task states.

### Tests

- T4: deployment/runtime checks.
- T5: Codex + Cursor E2E.

### Acceptance

The expected v0.1 lifecycle can be reproduced without manual repair.

---

## MA2-BL-006 — Freeze Executor Security Baseline
### Status: DONE

Priority: **P0**

Dependencies: MA2-BL-002.

### Objective

Record the security guarantees that are already closed and the two explicitly
open findings.

### Implementation

Ensure the executor audit distinguishes:

```text
PASS — existing host/mount/socket/path guarantees
WARN — credential confidentiality from task shell
WARN — destination-level task egress restriction
FAIL — regression of a closed invariant
```

Do not convert open findings into PASS before runtime enforcement exists.

### Tests

- T2: source audit regression.
- T4: manual executor boundary audit where applicable.
- T3.

### Acceptance

Future security closure can turn specific WARN findings into PASS without
losing visibility into existing guarantees.

---

# EPIC 1 — Mechanical Broker Modularization

Goal: split the current monolithic broker without changing external behavior.

---

## MA2-CORE-001 — Introduce Importable `agentdev` Package Skeleton
### Status: DONE

Priority: **P0**

Dependencies: EPIC 0 complete.

### Objective

Create the target package without changing broker behavior.

### Implementation

Introduce:

```text
platform-src/agentdev/
    __init__.py
    broker/
    core/
    execution/
    policy/
    runtime/
    agents/
```

Keep `platform-src/bin/agentd` and `agentctl` as executable entrypoints.

At this stage they may delegate only selected helpers.

### Tests

- T0: package imports compile.
- T3.
- regression asserting deployed entrypoints remain present/executable.

### Acceptance

The source package exists and the current CLI/broker behavior is unchanged.

---

## MA2-CORE-002 — Extract Shared Validation and Domain Models
### Status: DONE

Priority: **P0**

Dependencies: MA2-CORE-001.

### Objective

Move provider-neutral identifiers, path validation, and shared data structures
out of `agentd`.

### Implementation

Extract as appropriate:

```text
name validation
Git branch validation
canonical file/dir validation
ProjectContext
TaskContext
ProviderStateSpec placeholder
ExecutorSpec placeholder
```

Do not move provider-specific state semantics yet.

### Tests

- T1: validation boundary cases.
- existing symlink/path rejection regressions.
- T3.

### Acceptance

No behavior changes in path validation or request naming rules.

---

## MA2-CORE-003 — Extract Project and Git Handoff Services
### Status: DONE

Priority: **P0**

Dependencies: MA2-CORE-002.

### Objective

Move project initialization/synchronization/export and Git bundle operations
into provider-neutral core modules.

### Implementation

Extract:

```text
project_paths
project-init
project-sync
project-export
project-status Git operations
bundle validation
integration branch checks
```

Human-side Git separation remains unchanged.

### Tests

- T1/T2: project initialization and bundle handling.
- existing `security-regression.py`.
- existing Git model smoke.
- T3.

### Acceptance

No provider module is imported by project/Git services.

---

## MA2-CORE-004 — Extract Task, Dependency, and Worktree Services
### Status: DONE

Priority: **P0**

Dependencies: MA2-CORE-002.

### Objective

Move task lifecycle out of the broker entrypoint.

### Implementation

Extract:

```text
task-start
task-complete
task-abort
task-merge
task-list
dependency validation
parallel worktree creation/removal
recorded-head merge validation
```

### Tests

- existing parallel lifecycle regression;
- dependency semantics regression;
- security regression for recorded-head merge;
- T3.

### Acceptance

Sequential and parallel task state machines remain byte-for-byte compatible
where practical and semantically compatible everywhere.

---

## MA2-CORE-005 — Extract Locking Service
### Status: DONE

Priority: **P0**

Dependencies: MA2-CORE-002.

### Objective

Make concurrency control a provider-neutral service.

### Implementation

Extract lock path creation, shared/exclusive locking, and integration/task lock
selection helpers.

Do not move provider run policy into the locking module.

### Tests

- existing locking/concurrency regression;
- new unit tests for lock-name validation;
- T3.

### Acceptance

Concurrent lifecycle behavior is unchanged.

---

## MA2-CORE-006 — Extract Broker RPC Server Boundary
### Status: DONE

Priority: **P0**

Dependencies: MA2-CORE-003, MA2-CORE-004, MA2-CORE-005.

### Objective

Make `platform-src/bin/agentd` a thin process entrypoint.

### Implementation

Move:

```text
request decoding
request-shape validation
operation dispatch
response framing
socket lifecycle
```

into `agentdev/broker`.

Do not redesign RPC yet.

### Tests

- MA2-BL-003 characterization suite;
- broker dispatch unit tests;
- T3.

### Acceptance

Existing `agentctl` can communicate with the refactored broker without changes.

---

# EPIC 2 — Agent Driver Contract

Goal: remove provider identity branching from generic broker execution.

---

## MA2-DRV-001 — Define `AgentCapabilities`, `RunSpec`, and `AgentDriver`
### Status: DONE

Priority: **P0**

Dependencies: EPIC 1 complete.

### Objective

Introduce the provider integration contract without migrating providers yet.

### Implementation

Define immutable/validated data structures for at least:

```text
AgentCapabilities
ProviderStateSpec
ProviderPolicyArtifacts
RunSpec
AuthSpec
VersionProbeSpec
```

Define `AgentDriver` methods for:

```text
id
display_name
capabilities
state_spec
installation/build metadata
version probe
auth
auth status
policy compilation
run spec construction
```

Keep interfaces minimal; do not add future-cloud abstractions not required by
the architecture.

### Tests

- T1: model validation/serialization where applicable.
- contract tests using a fake driver.
- T3.

### Acceptance

The interface can represent all current Codex and Cursor behavior without
provider-specific fields in generic models.

---

## MA2-DRV-002 — Implement Trusted `AgentRegistry`
### Status: DONE

Priority: **P0**

Dependencies: MA2-DRV-001.

### Objective

Replace hard-coded provider enumeration with a trusted in-tree registry at the
broker level.

### Implementation

The registry must:

```text
register trusted built-in drivers
reject duplicate IDs
reject invalid IDs
return a driver by ID
list enabled/known drivers deterministically
```

Do not dynamically import arbitrary user-provided modules.

### Tests

- T1: registration, duplicate rejection, lookup, deterministic ordering.
- T2: unknown provider rejection through broker.
- T3.

### Acceptance

Generic broker code no longer needs `ALLOWED_PROVIDERS`.

---

## MA2-DRV-003 — Move Provider State Semantics Behind Driver Contract
### Status: DONE

Priority: **P0**

Dependencies: MA2-DRV-002.

### Objective

Remove `.codex` / `.cursor` state-path knowledge from generic broker/runtime
code.

### Implementation

Move into drivers or provider state adapters:

```text
state directory
state volume naming metadata
legacy migration metadata
policy file target
provider-specific state reconciliation hooks
```

Generic runtime receives a `ProviderStateSpec`.

### Tests

- refactor `provider-state-layout-regression.py` to consume driver specs;
- migration regression for existing volumes;
- T3.

### Acceptance

A fake third provider with a different state path can be represented without
editing generic runtime code.

---

## MA2-DRV-004 — Extract `CodexDriver`
### Status: DONE

Priority: **P0**

Dependencies: MA2-DRV-003.

### Objective

Move all Codex-specific execution semantics behind `CodexDriver`.

### Implementation

Move:

```text
Codex auth command
Codex status command
version probe
codex exec construction
read-only/workspace-write behavior
outer-only compatibility behavior
Codex policy mount/config handling
Codex state metadata
```

Preserve current behavior before policy-model migration.

### Tests

- MA2-BL-004 Codex characterization tests;
- Codex driver unit tests;
- current provider state regression;
- T3;
- T5 smoke after deployment.

### Acceptance

No generic broker operation contains `provider == "codex"`.

---

## MA2-DRV-005 — Extract `CursorDriver`
### Status: DONE

Priority: **P0**

Dependencies: MA2-DRV-003.

### Objective

Move all Cursor-specific execution semantics behind `CursorDriver`.

### Implementation

Move:

```text
Cursor auth command
Cursor status command
version probe
headless run construction
trust behavior
state metadata
policy reconciliation
Cursor-native configuration handling
```

Preserve current behavior before policy-model migration.

### Tests

- MA2-BL-004 Cursor characterization tests;
- existing Cursor policy reconciliation regression;
- provider state regression;
- T3;
- T5 smoke after deployment.

### Acceptance

No generic broker operation contains Cursor-specific command/config logic.

---

## MA2-DRV-006 — Add Fake Driver Extensibility Regression
### Status: DONE

Priority: **P0**

Dependencies: MA2-DRV-004, MA2-DRV-005.

### Objective

Prove that the architecture, not just the implementation, supports another
provider.

### Implementation

Create a test-only fake driver with:

```text
different state target
different executable name
different version probe
different auth command
minimal capability set
```

Do not ship it as a production provider.

### Tests

- T1/T2: registry, status, version, run-spec generation through generic broker.
- source audit that generic broker files contain no recognized provider command
  names.
- T3.

### Acceptance

Adding the fake driver requires no modification to project/task/Git/runtime
core.

---

# EPIC 3 — Runtime Backend Extraction

Goal: separate provider semantics from Podman process construction.

---

## MA2-RT-001 — Define `ResolvedExecutionPlan`
### Status: DONE

Priority: **P0**

Dependencies: EPIC 2 complete.

### Objective

Create one validated representation of everything required to start an
executor.

### Implementation

Represent at least:

```text
agent ID
image
command/arguments
environment
workspace mount
reference mounts
task metadata mount
provider state mounts
provider policy artifacts
resource limits
network runtime requirements
read-only/read-write mode
interaction mode
security class placeholder
required capabilities placeholder
```

Do not place raw project path authorization decisions inside provider drivers.

### Tests

- T1 validation tests.
- reject contradictory mount modes or missing required fields.
- T3.

### Acceptance

Broker execution can be expressed as plan creation followed by runtime
execution.

---

## MA2-RT-002 — Define `RuntimeBackend`
### Status: DONE

Priority: **P0**

Dependencies: MA2-RT-001.

### Objective

Introduce an execution backend contract without adding remote execution.

### Implementation

Contract must cover only the current needs:

```text
execute noninteractive
execute interactive/PTTY
terminate/cancel
image existence probe
build invocation support if retained in backend
```

Keep remote/cloud concepts out of v0.2 implementation.

### Tests

- T1 fake backend tests.
- T3.

### Acceptance

Generic execution service depends on `RuntimeBackend`, not directly on
`subprocess podman`.

---

## MA2-RT-003 — Extract `PodmanBackend`
### Status: DONE

Priority: **P0**

Dependencies: MA2-RT-002.

### Objective

Move generic Podman argument construction and execution out of broker/provider
code.

### Implementation

Own:

```text
--read-only
--cap-drop=all
no-new-privileges
tmpfs
cgroup/resource limits
mount serialization
working directory
cidfile
network namespace flags
process launch
```

Provider CLI arguments remain opaque command payload.

### Tests

- T2 exact runtime argument tests;
- existing executor boundary source audit adapted to backend;
- provider-state mount regression;
- T3;
- T4 smoke.

### Acceptance

No provider driver invokes `podman`.

No Podman backend code branches on provider identity.

---

## MA2-RT-004 — Move Interactive/Noninteractive Streaming Behind Runtime Boundary
### Status: DONE

Priority: **P1**

Dependencies: MA2-RT-003.

### Objective

Prevent PTY/process-control mechanics from remaining entangled with provider
logic.

### Implementation

Move:

```text
PTY setup
window resize
process-group termination
SIGINT/SIGTERM handling
stdout streaming
exit framing callbacks
cidfile cleanup
```

behind execution/runtime services.

### Tests

- T1/T2 with subprocess stubs;
- cancellation regression;
- interactive framing regression;
- T3.

### Acceptance

Provider drivers only declare interaction requirements.

---

## MA2-RT-005 — Preserve GitNexus as Non-Agent Runtime Consumer
### Status: DONE

Priority: **P1**

Dependencies: MA2-RT-003.

### Objective

Ensure optional GitNexus indexing continues to use the runtime layer without
being modeled as an `AgentDriver`.

### Implementation

Keep GitNexus outside the agent registry unless future requirements justify a
different abstraction.

### Tests

- existing index behavior regression;
- optional-image failure remains non-fatal;
- T3.

### Acceptance

Multi-agent modularization does not incorrectly turn every containerized tool
into a provider driver.

---

# EPIC 4 — Provider-Neutral Policy Model

Goal: make broker-owned execution policy the source of security semantics.

---

## MA2-POL-001 — Define `ExecutionPolicy` Schema
### Status: DONE

Priority: **P0**

Dependencies: EPIC 3 complete.

### Objective

Represent execution intent independently of provider configuration.

### Implementation

Initial schema must cover:

```text
workspace access
reference access
external filesystem access
task-shell network mode
network destinations
provider-auth task visibility
Git read/commit/push
sandbox required
resource limits
security class
```

Use `task_shell` as the canonical execution-plane key for both task networking
and provider-auth visibility. Do not introduce a generic `network.task` alias.

Resource memory must be validated and normalized into a comparable integer byte
quantity at the policy boundary. Accept positive integer bytes or positive
integer `k`/`m`/`g`/`t` quantities using 1024-based units; reject zero, negative,
fractional, and arbitrary text values. Do not change the completed runtime
`ResourceLimits` contract merely to store policy normalization.

Avoid fields named after provider-native settings.

### Tests

- T1 valid/invalid schema cases.
- unknown field rejection.
- deterministic normalization.
- T3.

### Acceptance

Review/implement/dependency/compatibility profiles can all be represented
without provider-specific fields.

---

## MA2-POL-002 — Implement Monotonic `PolicyResolver`
### Status: DONE

Priority: **P0**

Dependencies: MA2-POL-001.

### Objective

Resolve:

```text
platform baseline
project policy
execution profile
run restrictions
```

without allowing lower layers to widen hard restrictions.

### Implementation

Use a complete `ExecutionPolicy` as the platform baseline. Treat project
policy, execution profile, and run restrictions as sparse restriction layers
using the same nested policy field names; omitted fields inherit the current
effective value.

Define explicit monotonic semantics for every field:

```text
access/visibility/network rights    lower permission is more restrictive
Git permission booleans             false is more restrictive
sandbox.required                    true is more restrictive
resource ceilings                   smaller is more restrictive
security class                      hardened is stronger than compatibility
network allowlist                   lower set must be a subset
```

Normalize each candidate layer through `ExecutionPolicy` before accepting it.
Reject unknown sparse fields and contradictory escalation attempts rather than
silently clamping them.

### Tests

- T1 matrix of narrowing and forbidden widening cases.
- property-style tests where practical: resolved policy must never be more
  permissive than a hard upper-layer restriction.
- T3.

### Acceptance

A project/run cannot override platform hard denials.

---

## MA2-POL-003 — Add Built-In Execution Profiles
### Status: DONE

Priority: **P0**

Dependencies: MA2-POL-002.

### Objective

Implement the four architecture profiles:

```text
review
implement
dependency
compatibility
```

### Implementation

Store profile definitions as broker-owned trusted configuration.

Profile meanings must match the architecture document. Review, implement, and
dependency are sparse operational restriction layers and do not select a
security class; fields not specified by a profile inherit the effective upper
policy. Dependency profile materialization requires an explicit
task-shell destination allowlist supplied by the caller; public package
registries are not hard-coded into the profile. The explicit compatibility
profile selects `security_class=compatibility` without weakening unrelated
upper-layer controls; a hardened upper-layer requirement therefore rejects it
through the monotonic resolver.

### Tests

- T1 snapshot/semantic tests for every profile.
- explicit checks that review is workspace read-only;
- implement has workspace write but task network deny;
- dependency requires destination allowlist;
- compatibility is not equivalent to hardened.
- T3.

### Acceptance

Profiles resolve deterministically to provider-neutral policies.

---

## MA2-POL-004 — Implement Capability Requirement Matching
### Status: DONE

Priority: **P0**

Dependencies: MA2-POL-003, MA2-DRV-001.

### Objective

Reject agents that cannot satisfy a requested policy/security class.

### Implementation

Compute required capabilities from the resolved policy.

Return a precise error listing missing capabilities.

Capability matching must also validate the generic capability requirements
already carried by `ResolvedExecutionPlan` before runtime execution. This keeps
the current compatibility run path fail-closed while the profile-based public
run interface is introduced by later requirements.

No silent downgrade is permitted.

### Tests

- T1 capability matching matrix;
- fake driver missing one requirement;
- hardened-to-compatibility downgrade rejection;
- T3.

### Acceptance

Every run is capability-checked before runtime plan execution.

---

## MA2-POL-005 — Map Legacy Run Flags to Profiles
### Status: DONE

Priority: **P1**

Dependencies: MA2-POL-003.

### Objective

Preserve existing CLI/RPC behavior while introducing profiles.

### Implementation

Define explicit compatibility mappings for current:

```text
readonly
outer_only
```

The mapping must be documented and deterministic. Legacy `readonly=true` selects
review intent while writable legacy execution selects implement intent. Existing
legacy runs remain compatibility-class during migration, and `outer_only` remains
an orthogonal provider compatibility modifier so the existing read-only +
outer-only combination stays valid.

The compatibility adapter also accepts a direct profile identifier for reuse by
the later profile-based CLI. Equivalent aliases are accepted deterministically; a
direct profile request and legacy aliases with contradictory workspace semantics
are rejected rather than ambiguously merged. POL-005 does not add a public
`profile` RPC field or CLI option; that interface remains MA2-CLI-004.

### Tests

- T1/T2 mapping matrix.
- old CLI regression.
- conflict rejection.
- T3.

### Acceptance

Existing scripts continue to work during migration.

---

## MA2-POL-006 — Implement Codex Policy Compiler
### Status: DONE

Priority: **P0**

Dependencies: MA2-POL-004, MA2-DRV-004.

### Objective

Translate resolved platform policy into Codex-native execution controls.

### Implementation

The adapter may generate:

```text
Codex sandbox selection
managed/immutable configuration
filesystem restrictions
environment filtering
network controls
approval configuration
```

Only capabilities actually verified against the supported Codex version may be
advertised. The implementation baseline is the pinned Codex CLI 0.147.0 image.
The compiler translates `ExecutionPolicy` workspace sandboxing, noninteractive
approval behavior, and task-shell deny/allow/allowlist network controls while
preserving the legacy readonly/outer-only dictionary path. Hardened security
classes and network/credential policy capabilities remain unadvertised until
the later authenticated/adversarial acceptance requirements provide evidence.

### Tests

- T1 provider-specific translation tests.
- T2 run-spec regression.
- unsupported-capability fail-closed tests.
- T3.
- T5/T6 later in security epic.

### Acceptance

Generic policy code contains no Codex configuration semantics.

---

## MA2-POL-007 — Implement Cursor Policy Compiler
### Status: DONE

Priority: **P0**

Dependencies: MA2-POL-004, MA2-DRV-005.

### Objective

Translate resolved platform policy into Cursor-native execution controls.

### Implementation

Separate:

```text
Cursor permissions/approval policy
Cursor sandbox policy
broker-controlled platform policy
Cursor-managed mutable fields
```

Extend reconciliation only for fields explicitly declared platform-managed.
The current implementation keeps `cli-config.json` reconciliation restricted to
the `permissions` object and preserves Cursor-managed fields outside it. Resolved
policies use Cursor's documented `--sandbox enabled|disabled` control only for
combinations that can be represented without inventing unverified semantics.
Per-run destination allowlists remain fail-closed and are completed by
MA2-SEC-007; this requirement does not advertise hardened egress or credential
confidentiality.

### Tests

- existing reconciliation regression;
- new sandbox-policy translation tests;
- preservation of Cursor-managed fields;
- atomic replacement/mode tests;
- T3.
- T5/T6 later in security epic.

### Acceptance

Generic policy code contains no Cursor configuration semantics.

---

## MA2-POL-008 — Add Canonical Policy Serialization and Hash
### Status: DONE

Priority: **P1**

Dependencies: MA2-POL-002.

### Objective

Produce a stable representation of the resolved policy for audit/provenance.

### Implementation

Canonical serialization must:

```text
be deterministic
include policy schema version
exclude transient runtime values
produce a stable hash
```

The implemented representation is compact UTF-8 JSON over the normalized
`ExecutionPolicy.as_dict()` value with recursively sorted object keys and no
formatting whitespace. The fingerprint format is `sha256:<64 lowercase hex>`
and hashes those canonical UTF-8 bytes. Runtime/provider identity, generated
provider artifacts, image IDs, paths, timestamps, and run metadata are excluded
because the serializer accepts only a resolved `ExecutionPolicy`.

### Tests

- T1 stable hash across key ordering/input formatting;
- hash changes when effective policy changes;
- T3.

### Acceptance

Run provenance can reference the exact effective policy without storing
provider-specific generated files as the source of truth.

---

# EPIC 5 — Security Closure

Goal: close credential confidentiality and destination-level task egress for
hardened profiles.

This epic must use adversarial runtime testing.

---

## MA2-SEC-001 — Define Provider-Neutral Hardened Security Contract
### Status: DONE

Priority: **P0**

Dependencies: EPIC 4 complete.

### Objective

Turn the architecture's CAN/CANNOT list into an executable provider-neutral
contract suite.

### Implementation

The harness must express tests for:

```text
CAN read workspace
CAN write workspace when profile permits
CAN run tests
CAN commit when profile permits

CANNOT read human checkout
CANNOT read host credentials
CANNOT read provider auth/session credentials
CANNOT inspect/traverse trusted control-process state through procfs
CANNOT write outside allowed filesystem
CANNOT access runtime socket
CANNOT access arbitrary task-shell Internet
CANNOT access private/loopback/metadata destinations
```

Separate profile-specific expectations.

The implemented common harness lives under `tests/contracts/` and defines only
observable probe identifiers and allow/deny expectations. It contains no Codex,
Cursor, provider-state path, CLI, Podman, or native-sandbox knowledge. Review,
implement, and dependency each receive an explicit hardened contract;
compatibility is rejected as a hardened contract. Missing probe observations fail
closed. The common contract also denies control-process `environ`, `cmdline`,
file-descriptor, filesystem-traversal, and memory-read channels without embedding
provider-specific PID/path knowledge. Provider/runtime-specific T6 adapters are
added by the later SEC cases.

### Tests

This requirement creates T6 infrastructure itself.

Unit-test the harness with a fake executor result adapter.

### Acceptance

The same contract can be executed against Codex and Cursor without embedding
provider paths in the generic test definitions.

---

## MA2-SEC-002 — Enforce Codex Credential Confidentiality
### Status: DONE

Priority: **P0**

Dependencies: MA2-SEC-001, MA2-POL-006.

### Objective

Allow the Codex control process to authenticate while preventing
model-generated task subprocesses from retrieving Codex auth/session secrets in
hardened profiles.

### Implementation

Cover the architecture-required channels:

```text
environment
filesystem
process inheritance
inherited descriptors where relevant
provider state
generated configuration
logs/output
control-process procfs inspection/traversal
```

The hardened trust model distinguishes the outer provider control process from the
model-generated task subprocess. The outer control process is a trusted platform
component that may retain provider authentication state and the minimum runtime
interfaces needed to construct an inner sandbox. The task subprocess is the
untrusted boundary to which CAN/CANNOT security expectations apply.

This distinction does not make broad system-information exposure acceptable.
Default outer procfs masking remains defense in depth unless a nested-sandbox
backend proves that a narrower bootstrap exception is required. Any such exception
must terminate at the inner boundary: the task gets a fresh PID namespace/procfs
and must not inspect or traverse the outer control process.

`outer-only` must not be advertised as satisfying this guarantee unless it
actually does.

#### Certified Codex 0.147.0 credential boundary

The pinned Codex integration runs the trusted provider control process under the
non-root `1000:1000` identity, retains `cap-drop=all` and
`no-new-privileges`, and requests the provider-neutral nested-sandbox bootstrap
mechanism only when Codex must construct its native task sandbox. Scoped Codex
state is mounted under `/home/node/.codex`.

Policy-based native-sandbox execution activates the pinned credential
confidentiality profile. The profile denies both `/home/node/.codex` and
`/home/node/.codex/**`, applies task-shell environment/history controls, and
fails closed when provider-auth task-shell denial is requested without
provider-native sandboxing. Legacy `outer-only` remains compatibility execution
and does not satisfy SEC-002.

The deployed authenticated T5/T6 proof passed against the production `codex exec`
path. T5 verified that persisted Codex authentication and model-generated native
sandbox execution remain functional. T6 first established a synthetic negative
control that was readable from the trusted control side, then verified that the
task could not retrieve provider state, the secret-shaped task environment value,
inherited descriptors, or trusted control-process `environ`, `cmdline`, `fd`,
`root`/`cwd`, and `mem` channels. The task also observed task-local process state,
and the synthetic sentinel did not leak through captured task output.

This evidence closes the Codex credential-confidentiality requirement and permits
the driver to advertise `provider_state_protection`. It does not certify the full
`hardened` security class: destination-level task-egress closure remains pending
MA2-SEC-005/MA2-SEC-006.

### Tests

- T1/T2 adapter tests.
- T5 authenticated Codex run.
- T6 adversarial task attempts against every relevant secret channel.
- T6 control-process procfs attempts: `environ`, `cmdline`, `fd`, `root`/`cwd`,
  and `mem` where applicable.
- verify the inner task sees only task-local process state and cannot traverse to
  the outer provider control compartment.
- verify authentication still works.
- T3.

### Acceptance

Codex advertises `provider_state_protection` after the authenticated deployed
T5/T6 proof passes. Full `hardened` advertising remains blocked until the
common credential and egress requirements are certified.

---

## MA2-SEC-003 — Enforce Cursor Credential Confidentiality

Priority: **P0**

Dependencies: MA2-SEC-001, MA2-POL-007.

### Objective

Allow Cursor authentication while preventing model-generated task subprocesses
from retrieving Cursor auth/session secrets in hardened profiles.

### Implementation

Use Cursor-native sandbox controls and broker-generated enforcement as
supported by the pinned/current CLI.

Do not assume that `permissions` alone are a credential boundary.

### Tests

- T1/T2 adapter/reconciliation tests.
- T5 authenticated Cursor run.
- T6 adversarial secret access attempts.
- verify authentication still works.
- T3.

### Acceptance

Cursor may advertise hardened credential confidentiality only after T6 passes.

---

## MA2-SEC-004 — Make Compatibility Security Semantics Explicit

Priority: **P0**

Dependencies: MA2-SEC-002, MA2-SEC-003.

### Objective

Prevent compatibility execution from being confused with hardened execution.

### Implementation

Ensure run metadata/status/output identifies the effective security class.

Legacy `--outer-only` must resolve to compatibility semantics unless all
hardened requirements are actually satisfied.

### Tests

- T1/T2 downgrade rejection.
- CLI/RPC regression.
- T3.

### Acceptance

A hardened request can never execute through a compatibility plan silently.

---

## MA2-SEC-005 — Define Provider-Neutral Task Egress Contract

Priority: **P0**

Dependencies: MA2-SEC-001.

### Objective

Create adversarial tests for task-shell network policy.

### Implementation

The harness must test at least:

```text
network-denied profile blocks ordinary public destination
dependency profile allows explicit destination
non-allowlisted destination is blocked
loopback is blocked unless explicitly allowed
RFC1918/private destinations are blocked
link-local/cloud metadata destination is blocked
raw-IP bypass is blocked
IPv6 equivalents are covered where supported
redirect to denied destination does not bypass policy
provider control-plane connectivity remains functional
```

Tests must use controlled test endpoints where possible rather than arbitrary
third-party hosts.

### Tests

T6 harness plus deterministic unit tests for policy resolution.

### Acceptance

Destination-level semantics are observable and provider-neutral.

---

## MA2-SEC-006 — Enforce Codex Task-Shell Egress Policy

Priority: **P0**

Dependencies: MA2-SEC-005, MA2-POL-006.

### Objective

Make Codex task-shell networking deny-by-default and destination-limited when
explicitly enabled.

### Implementation

Use supported Codex-native network controls and/or broker/runtime enforcement
required to satisfy the common contract.

Provider API/control traffic must not imply unrestricted child command traffic.

### Tests

- T1/T2 policy translation.
- T5 authenticated run.
- T6 deny/allowlist/private/raw-IP/redirect cases.
- T3.

### Acceptance

Codex hardened profiles pass the common egress contract.

---

## MA2-SEC-007 — Enforce Cursor Task-Shell Egress Policy

Priority: **P0**

Dependencies: MA2-SEC-005, MA2-POL-007.

### Objective

Make Cursor task-shell networking deny-by-default and destination-limited when
explicitly enabled.

### Implementation

Use Cursor-native sandbox/network configuration and broker-controlled policy
translation.

### Tests

- T1/T2 translation/reconciliation.
- T5 authenticated run.
- T6 common egress contract.
- T3.

### Acceptance

Cursor hardened profiles pass the common egress contract.

---

## MA2-SEC-008 — Gate Hardened Capability Advertising on Acceptance Evidence

Priority: **P0**

Dependencies: MA2-SEC-002, MA2-SEC-003, MA2-SEC-006, MA2-SEC-007.

### Objective

Prevent a driver from claiming hardened support based only on static
configuration.

### Implementation

Define supported hardened capabilities in trusted code/config only after the
required acceptance suite exists and passes for the supported provider version.

Version changes that invalidate tested semantics must require revalidation.

### Tests

- T1 driver capability gating.
- version mismatch/revalidation behavior.
- T3.

### Acceptance

Capability declarations correspond to tested platform guarantees.

---

## MA2-SEC-009 — Convert Executor Audit WARN Findings to Contract-Aware Results

Priority: **P1**

Dependencies: MA2-SEC-008.

### Objective

Update the old source-oriented executor audit for the new architecture.

### Implementation

The audit should distinguish:

```text
outer-runtime guarantees
provider-driver policy guarantees
hardened contract evidence
compatibility limitations
```

Remove hard-coded `.codex` / `.cursor` assumptions from generic audit logic.

### Tests

- T2 source/plan audit.
- T3.
- T6 remains the authoritative runtime proof for hardened claims.

### Acceptance

The audit no longer reports unresolved credential/egress WARN for a provider
that demonstrably passes the hardened contract, while compatibility modes
remain clearly weaker.

---

# EPIC 6 — Run Provenance

Goal: make multiple agent runs on one requirement auditable.

---

## MA2-RUN-001 — Define Run Record Schema

Priority: **P1**

Dependencies: EPIC 5 complete, MA2-POL-008.

### Objective

Represent an individual agent execution separately from task lifecycle.

### Implementation

Record at least:

```text
run_id
project
task
agent
agent version
image ID
profile
security class
policy hash
base commit
result commit where applicable
started_at
finished_at
exit code
terminal status
```

Schema must be versioned.

### Tests

- T1 serialization/validation.
- backward/unknown-version rejection strategy.
- T3.

### Acceptance

A task can reference multiple independent runs without changing task state
semantics.

---

## MA2-RUN-002 — Persist Run Records Atomically

Priority: **P1**

Dependencies: MA2-RUN-001.

### Objective

Persist provenance safely under project state.

### Implementation

Choose a stable path under the existing project namespace.

Use atomic write/replace semantics.

Do not permit task subprocesses to modify broker-owned provenance.

### Tests

- T1/T2 atomic write and path validation.
- interruption/failure simulation.
- symlink rejection.
- T3.

### Acceptance

Partial writes cannot create a valid-looking completed run record.

---

## MA2-RUN-003 — Record Success, Failure, and Cancellation

Priority: **P1**

Dependencies: MA2-RUN-002.

### Objective

Ensure provenance exists for every started broker-managed run.

### Implementation

Record:

```text
start
normal exit
provider error
runtime error
user cancellation
```

Do not equate run failure with automatic task abort.

### Tests

- T2 execution result matrix.
- cancellation regression.
- T3.

### Acceptance

Every broker-started run has a terminal provenance state.

---

## MA2-RUN-004 — Capture Effective Version/Image/Commit/Policy Identity

Priority: **P1**

Dependencies: MA2-RUN-003.

### Objective

Make runs reproducibly attributable to the execution environment.

### Implementation

Capture observed image ID and provider version where available rather than only
configured tags.

Record the resolved policy hash and base/result commits.

### Tests

- T2 mocked probes.
- T4 real build/run verification.
- T3.

### Acceptance

A reviewer can determine what provider/runtime/policy/commit context produced a
run.

---

# EPIC 7 — Registry-Driven CLI

Goal: remove provider enumeration from the frontend.

---

## MA2-CLI-001 — Add Broker Agent Discovery Operation

Priority: **P1**

Dependencies: MA2-DRV-002, EPIC 6 complete.

### Objective

Expose trusted registry information through the broker.

### Implementation

Return deterministic agent metadata such as:

```text
id
display name
enabled/disabled
availability
authentication status where requested separately
supported profiles/security classes
```

Do not expose secrets or provider-state paths unnecessarily.

### Tests

- T1/T2 registry response.
- unknown/disabled agents.
- T3.

### Acceptance

The frontend can discover agents without a compile-time list.

---

## MA2-CLI-002 — Add `agentctl agents`

Priority: **P1**

Dependencies: MA2-CLI-001.

### Objective

Provide human-readable and machine-readable agent discovery.

### Implementation

Support stable JSON output and a concise human/oneline form if useful.

### Tests

- CLI parsing/output regression.
- deterministic ordering.
- T3.

### Acceptance

Adding a new enabled in-tree driver requires no `agentctl` provider-list code
change.

---

## MA2-CLI-003 — Make Auth/Status/Versions Registry-Driven

Priority: **P1**

Dependencies: MA2-CLI-001.

### Objective

Remove current Codex/Cursor loops and branches from generic broker/frontend
operations.

### Implementation

Iterate enabled registered drivers and invoke driver contracts.

Preserve useful per-provider error reporting.

### Tests

- fake third driver regression;
- Codex/Cursor characterization tests;
- T3;
- T4.

### Acceptance

Generic status/version/auth infrastructure contains no fixed provider tuple.

---

## MA2-CLI-004 — Add Profile-Based Run Interface

Priority: **P1**

Dependencies: MA2-POL-005, MA2-CLI-001.

### Objective

Expose intent-oriented execution:

```bash
agentctl run <project> <task> --agent <id> --profile <profile> <prompt>
```

### Implementation

Preserve legacy invocation during migration.

Reject contradictory legacy/profile combinations.

### Tests

- CLI parser tests.
- RPC serialization tests.
- legacy compatibility regression.
- T3.
- T5 review/implement flows.

### Acceptance

New workflows no longer need provider-native security flags.

---

## MA2-CLI-005 — Add Run Inspection Only if Operationally Needed

Priority: **P2**

Dependencies: EPIC 6 complete.

### Objective

Expose run provenance without adding scheduling semantics.

### Implementation

If needed, add:

```text
run-list
run-status
```

Read-only inspection only.

### Tests

- T1/T2 query/path authorization.
- T3.

### Acceptance

No command mutates task scheduling or queues work.

---

# EPIC 8 — Copilot Driver

Goal: validate extensibility with the first new production provider.

Before implementation, re-verify current GitHub Copilot CLI documentation and
the supported authentication/sandbox interfaces. Do not implement from stale
assumptions in this backlog.

---

## MA2-COP-001 — Define Copilot Provider Manifest and Verified Capability Baseline

Priority: **P1**

Dependencies: EPIC 7 complete.

### Objective

Document what the selected Copilot CLI version demonstrably supports.

### Implementation

Verify and record:

```text
installation source/version
noninteractive invocation
authentication method/state
version/status commands
filesystem sandbox capabilities
network sandbox capabilities
credential handling
structured output if used
```

Initially advertise only proven capabilities.

### Tests

- manifest/schema unit tests;
- documentation/source reference update;
- T3.

### Acceptance

No hardened capability is claimed from documentation alone without planned
acceptance coverage.

---

## MA2-COP-002 — Add Copilot Executor Image

Priority: **P1**

Dependencies: MA2-COP-001.

### Objective

Build a reproducible Copilot CLI executor on the existing base image.

### Implementation

Follow the same immutable-image principles as Codex/Cursor.

Pin versions as far as the vendor distribution mechanism reliably permits.

### Tests

- T0 Containerfile checks;
- T4 image build and version probe;
- package manifest/build-lock regression;
- T3.

### Acceptance

`agentctl build` and `versions` can include Copilot without affecting existing
providers.

---

## MA2-COP-003 — Implement Copilot State/Auth Adapter

Priority: **P1**

Dependencies: MA2-COP-002.

### Objective

Integrate authentication and scoped provider state.

### Tests

- T1/T2 state spec and auth command.
- migration behavior if any.
- T4 auth-status without model call where possible.
- T5 real auth.
- T3.

### Acceptance

Copilot state does not reuse Codex/Cursor state volumes or generic home mounts.

---

## MA2-COP-004 — Implement Copilot Noninteractive Run Driver

Priority: **P1**

Dependencies: MA2-COP-003.

### Objective

Run Copilot through the generic `AgentDriver`/`RuntimeBackend` path.

### Tests

- T1/T2 RunSpec generation.
- T5 disposable project run.
- generic lifecycle remains unchanged.
- T3.

### Acceptance

No core/domain modification is required to execute Copilot.

---

## MA2-COP-005 — Implement Copilot Policy Adapter and Compatibility Profile

Priority: **P1**

Dependencies: MA2-COP-004.

### Objective

Translate provider-neutral policy into supported Copilot controls.

### Tests

- T1/T2 translation.
- compatibility-mode T5.
- fail-closed test for unsupported hardened requirements.
- T3.

### Acceptance

Copilot works in explicit compatibility mode even before hardened certification,
if the available provider controls require that limitation.

---

## MA2-COP-006 — Certify Copilot Hardened Profiles if Supported

Priority: **P2**

Dependencies: MA2-COP-005, MA2-SEC-001, MA2-SEC-005.

### Objective

Advertise hardened Copilot support only if the common credential and egress
contracts pass.

### Tests

- full T6 credential contract;
- full T6 egress contract;
- T5 implementation/review;
- T3.

### Acceptance

If the provider cannot meet the contract, the requirement closes by explicitly
documenting the capability limitation rather than weakening the contract.

---

# EPIC 9 — Antigravity Driver

Goal: add Antigravity through the same extension mechanism.

Before implementation, re-verify the current Antigravity CLI because its CLI
and sandbox interfaces may evolve faster than the core architecture.

---

## MA2-AGY-001 — Define Antigravity Provider Manifest and Capability Baseline

Priority: **P1**

Dependencies: EPIC 8 architecture proof complete; Copilot hardened certification
is not required.

### Objective

Verify the current supported:

```text
installation/version
headless/noninteractive execution
authentication/state
sandbox behavior
network behavior
session behavior
structured output
```

### Tests

- manifest/schema tests;
- source/documentation references;
- T3.

### Acceptance

Unstable capabilities are marked explicitly rather than assumed.

---

## MA2-AGY-002 — Add Antigravity Executor Image

Priority: **P1**

Dependencies: MA2-AGY-001.

### Tests

- T0 Containerfile.
- T4 build/version.
- build manifest regression.
- T3.

### Acceptance

Existing providers remain unaffected.

---

## MA2-AGY-003 — Implement Antigravity State/Auth Adapter

Priority: **P1**

Dependencies: MA2-AGY-002.

### Tests

- T1/T2 state/auth.
- T4/T5 authentication flow.
- T3.

### Acceptance

Provider state is scoped independently.

---

## MA2-AGY-004 — Implement Antigravity Noninteractive Run Driver

Priority: **P1**

Dependencies: MA2-AGY-003.

### Tests

- T1/T2 RunSpec.
- T5 disposable task.
- T3.

### Acceptance

Antigravity uses existing broker/runtime/policy interfaces.

---

## MA2-AGY-005 — Implement Antigravity Policy Adapter and Explicit Capability Limits

Priority: **P1**

Dependencies: MA2-AGY-004.

### Objective

Translate supported policy controls without emulating missing guarantees.

### Tests

- T1/T2 policy translation;
- missing-capability fail closed;
- compatibility T5;
- T3.

### Acceptance

Unsupported hardened requirements produce explicit rejection.

---

## MA2-AGY-006 — Certify Hardened Profiles if Supported

Priority: **P2**

Dependencies: MA2-AGY-005.

### Tests

- T6 common credential contract;
- T6 common egress contract;
- T5 implementation/review.

### Acceptance

Hardened capability advertising is evidence-based.

---

# EPIC 10 — Legacy Cleanup and Configuration Normalization

Goal: remove migration-only coupling after all provider abstractions are proven.

---

## MA2-CLN-001 — Remove Hard-Coded Provider Enumeration

Priority: **P1**

Dependencies: EPIC 9 complete.

### Objective

Remove remaining fixed Codex/Cursor provider lists from generic code and tests.

### Tests

- source audit for forbidden provider enumeration in generic modules;
- fake/new driver tests;
- T3.

### Acceptance

Provider IDs appear in provider modules, provider-specific tests, docs, and
configuration only where semantically necessary.

---

## MA2-CLN-002 — Normalize Platform Configuration Around Agent Registry

Priority: **P1**

Dependencies: MA2-CLN-001.

### Objective

Replace generic configuration structures that assume fixed image keys such as
`images.codex` / `images.cursor` with registry-oriented agent configuration.

### Implementation

Provide migration/backward compatibility for deployed v0.1 config where
required.

Do not mix optional non-agent tools such as GitNexus into the agent registry.

### Tests

- T1 config validation/migration.
- T2 build/version behavior.
- T4 upgrade deployment.
- T3.

### Acceptance

Adding an enabled provider does not require editing generic image-key code.

---

## MA2-CLN-003 — Remove Provider Branching from Generic Tests

Priority: **P1**

Dependencies: MA2-CLN-001.

### Objective

Move provider-specific assertions into driver suites and keep generic tests
capability/contract based.

### Tests

This is itself a test-architecture refactor; T3 must stay green throughout.

### Acceptance

Generic executor/security tests do not know `.codex`, `.cursor`, provider
binary names, or provider-specific CLI arguments.

---

## MA2-CLN-004 — Deprecate Legacy Provider-Specific Run Flags

Priority: **P2**

Dependencies: MA2-CLI-004, MA2-SEC-004.

### Objective

Remove or formally deprecate `--outer-only` and other provider-native concepts
from the generic public API.

### Implementation

Retain explicit compatibility profile semantics.

Provide a clear error/migration message if flags are removed.

### Tests

- CLI deprecation/removal regression.
- documentation examples.
- T3.

### Acceptance

The public API is expressed in platform concepts: agent, profile, security
class, project, task.

---

## MA2-CLN-005 — Update Documentation to Implemented v0.2 State

Priority: **P1**

Dependencies: MA2-CLN-001 through MA2-CLN-004.

### Objective

Make README and architecture documentation distinguish implemented behavior
from historical migration notes.

### Implementation

Update:

```text
README.md
multi-agent-architecture-v0.2.md status
executor-boundary-audit.md
SOURCES.md
CHANGELOG.md
```

Do not mark unsupported provider hardened modes as supported.

### Tests

- T0 link/file checks where useful.
- T3.

### Acceptance

Documentation accurately describes the implemented platform.

---

# EPIC 11 — v0.2 Pilot Gate

Goal: validate the complete architecture under realistic local use before
considering additional infrastructure.

---

## MA2-PIL-001 — Full Multi-Agent Acceptance Matrix

Priority: **P0 for v0.2 release**

Dependencies: EPIC 10 complete.

### Objective

Run the complete supported matrix.

### Required Matrix

For every enabled provider:

```text
build
version
auth/status
review profile
implement profile
compatibility profile if supported/needed
dependency profile if supported
run provenance
project/task lifecycle integration
```

For every provider advertising hardened:

```text
credential T6
egress T6
filesystem T6
runtime-socket T6
```

Cross-provider workflows must include at least:

```text
Cursor implement -> Codex review
Codex implement -> Cursor review
one workflow involving Copilot
one workflow involving Antigravity if enabled
parallel worktree execution with different providers
```

### Acceptance

All advertised capabilities are backed by recorded acceptance results.

---

## MA2-PIL-002 — Upgrade/Existing-State Acceptance

Priority: **P0 for v0.2 release**

Dependencies: MA2-PIL-001.

### Objective

Verify that a deployed v0.1 installation can move to v0.2 without unsafe manual
repair.

### Tests

Use disposable copies/state to validate:

```text
existing project metadata
existing agent repository
existing tasks
existing Codex state
existing Cursor state
legacy provider state migration
new registry/config format
```

### Acceptance

Upgrade either succeeds automatically or fails safely with explicit operator
instructions; it must not partially migrate trusted state silently.

---

## MA2-PIL-003 — Scope Review for Post-v0.2 Isolation

Priority: **P2**

Dependencies: MA2-PIL-001, MA2-PIL-002.

### Objective

Use pilot evidence to decide whether any of the following are justified:

```text
separate provider control process / task runner
common outer egress proxy
VM-grade isolation
remote execution backend
automatic provider routing
quota-aware routing
cloud-agent integration
```

### Implementation

Produce an architecture decision note only.

Do not implement these features as part of this requirement.

### Acceptance

Each candidate is classified:

```text
not needed
defer
prototype
required for next version
```

with evidence from pilot usage.

---

# 6. Dependency Summary

The critical path is:

```text
EPIC 0
  baseline freeze
      |
      v
EPIC 1
  broker modularization
      |
      v
EPIC 2
  AgentDriver + registry
      |
      v
EPIC 3
  RuntimeBackend + PodmanBackend
      |
      v
EPIC 4
  provider-neutral policy
      |
      v
EPIC 5
  credential + egress closure
      |
      v
EPIC 6
  run provenance
      |
      v
EPIC 7
  registry-driven CLI
      |
      v
EPIC 8
  Copilot
      |
      v
EPIC 9
  Antigravity
      |
      v
EPIC 10
  cleanup
      |
      v
EPIC 11
  pilot gate
```

Copilot and Antigravity must not be pulled ahead of the driver/runtime/policy
refactor merely to demonstrate provider count.

---

# 7. Recommended Requirement Execution Pattern

For implementation agents, use the following task pattern.

### Implementation task

```text
Implement <REQ-ID> only.

Read:
- docs/multi-agent-architecture-v0.2.md
- docs/multi-agent-v0.2-implementation-backlog.md
- the requirement's dependencies and affected tests.

Follow the repository regression-first/TDD workflow.

Do not implement later backlog requirements opportunistically.
Preserve existing lifecycle and security invariants.
Add or update the tests required by <REQ-ID>.
Run focused tests and ./tests/package-check.sh.
Update documentation only where <REQ-ID> changes observable behavior.
Commit the completed change with the requirement ID in the commit message.
```

### Independent review task

```text
Review <REQ-ID> against:
- docs/multi-agent-architecture-v0.2.md;
- docs/multi-agent-v0.2-implementation-backlog.md;
- the requirement acceptance criteria;
- the tests added or changed by the implementation.

Check for:
- accidental provider-specific logic in generic modules;
- silent security downgrade;
- missing negative tests;
- weakened existing trust-boundary tests;
- implementation beyond the requirement scope;
- backwards-compatibility regressions.

Do not modify the implementation during review unless explicitly requested.
```

### Security review task

```text
Review <REQ-ID> as an adversarial executor-boundary change.

Do not treat provider configuration as proof of enforcement.
Require observable tests for the claimed security property.
Verify fail-closed behavior and negative/bypass cases.
Confirm compatibility mode is not reported as hardened.
```

---

# 8. Test Runner Evolution

The deterministic package runner should evolve gradually rather than being
replaced all at once.

Target structure:

```text
tests/
    package-check.sh

    core/
    policy/
    runtime/
    agents/
        codex/
        cursor/
        copilot/
        antigravity/

    contracts/
        execution-profile-regression.py
        hardened-filesystem-regression.py
        hardened-credential-regression.py
        hardened-egress-regression.py

    e2e/
        ...
```

This directory reorganization is not itself a required early milestone.

Move tests only when the associated source boundary has stabilized.

`package-check.sh` remains the authoritative deterministic package runner.

External authenticated E2E and T6 tests may remain explicitly gated because
they depend on a deployed host, provider authentication, and controlled network
test infrastructure.

---

# 9. Release Gate Summary

v0.2 must not be declared complete until all of the following are true:

```text
[ ] current v0.1 lifecycle behavior remains accepted
[ ] project/Git/task core is provider-neutral
[ ] AgentDriver/AgentRegistry are authoritative
[ ] provider drivers do not invoke Podman directly
[ ] Podman backend contains no provider-specific execution logic
[ ] provider-neutral policy is authoritative
[ ] policy hierarchy is monotonic
[ ] hardened runs fail closed
[x] Codex hardened credential contract passes
[ ] Cursor hardened credential contract passes
[ ] Codex hardened egress contract passes
[ ] Cursor hardened egress contract passes
[ ] compatibility mode is explicitly weaker
[ ] run provenance is persisted
[ ] agentctl provider discovery is registry-driven
[ ] Copilot runs through the same architecture
[ ] Antigravity runs through the same architecture or is explicitly capability-limited
[ ] generic tests are provider-neutral
[ ] upgrade from v0.1 is accepted
[ ] full multi-agent pilot matrix is recorded
```

---

# 10. Suggested First Implementation Sequence

The first implementation batch should stop before any new provider is added:

```text
MA2-BL-001
MA2-BL-002
MA2-BL-003
MA2-BL-004
MA2-BL-005
MA2-BL-006

MA2-CORE-001
MA2-CORE-002
MA2-CORE-003
MA2-CORE-004
MA2-CORE-005
MA2-CORE-006

MA2-DRV-001
MA2-DRV-002
MA2-DRV-003
MA2-DRV-004
MA2-DRV-005
MA2-DRV-006

MA2-RT-001
MA2-RT-002
MA2-RT-003
MA2-RT-004
MA2-RT-005

MA2-POL-001
MA2-POL-002
MA2-POL-003
MA2-POL-004
MA2-POL-005
MA2-POL-006
MA2-POL-007
MA2-POL-008

MA2-SEC-001
MA2-SEC-002
MA2-SEC-003
MA2-SEC-004
MA2-SEC-005
MA2-SEC-006
MA2-SEC-007
MA2-SEC-008
MA2-SEC-009
```

Only after this sequence is stable should the project proceed to run provenance,
dynamic CLI, Copilot, and Antigravity integration.
