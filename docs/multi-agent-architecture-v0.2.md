# Agent Development Stack v0.2 — Modular Multi-Agent Architecture and Migration Roadmap

## 1. Purpose

This document defines the target v0.2 architecture for the local constrained coding-agent development stack and records the current migration state.

The current v0.1 implementation provides a strong foundation:

- broker-controlled execution through `agentd`;
- rootless Podman as the outer execution boundary;
- no raw Podman socket exposure to agents or the human operator;
- controlled Git history transfer between human and agent trust domains;
- sequential and parallel requirement execution;
- worktree lifecycle management;
- locking and dependency semantics;
- separate provider state for Codex and Cursor;
- cross-provider task execution and review;
- package and deployment acceptance checks.

The v0.2 architecture should preserve these capabilities while removing provider-specific execution semantics from the broker core.

The primary architectural goal is:

> Make Codex, Cursor, Antigravity, Copilot, and future coding agents interchangeable execution drivers without making project lifecycle, Git lifecycle, security policy, or runtime orchestration provider-specific.

The secondary goal is:

> Replace provider configuration as the source of security semantics with a broker-owned, provider-neutral execution policy.

The v0.2 design remains intentionally local and constrained. It is not intended to become a generic scheduler, CI platform, distributed agent framework, or arbitrary container orchestration system.

### Current implementation checkpoint

The baseline freeze, mechanical CORE modularization, AgentDriver extraction, and
runtime-backend extraction are complete through `MA2-RT-005`.

The implementation now has:

```text
thin agentctl/agentd compatibility entrypoints
packaged broker/controller implementation
dedicated broker RPC boundary

provider-neutral domain core:
    validation and domain models
    project and Git handoff services
    task, dependency, and worktree services
    locking service

provider boundary:
    AgentDriver and AgentCapabilities
    trusted in-tree AgentRegistry
    CodexDriver and CursorDriver
    provider-owned state/auth/version/run semantics

execution/runtime boundary:
    ResolvedExecutionPlan
    RuntimeBackend
    PodmanBackend
    provider-neutral interactive/noninteractive process control
    GitNexus as a non-agent runtime consumer
```

Phase 4 policy work is implemented through `MA2-POL-003`: `ExecutionPolicy`
provides the strict normalized provider-neutral schema, `PolicyResolver`
provides monotonic composition of the platform baseline with sparse project,
profile, and run restriction layers, and the trusted built-in review,
implement, dependency, and compatibility profiles are defined. Capability
requirement matching is next (`MA2-POL-004`). The existing credential-
confidentiality and destination-level task-egress findings remain explicit
later security work.


---

# 2. Architectural Principles

## 2.1 Preserve the Existing Outer Trust Boundary

The following v0.1 boundaries remain authoritative and should not be redesigned as part of the v0.2 migration:

```text
human operator
      |
   agentctl
      |
 constrained RPC
      |
   agentd
      |
 rootless Podman
      |
 agent executor
```

The broker remains the only component allowed to construct executor environments.

Agents must not receive:

- a raw Podman/Docker socket;
- arbitrary host mount control;
- access to the canonical human checkout;
- unrestricted project discovery;
- arbitrary executor image selection;
- arbitrary provider-state mount selection.

---

## 2.2 Separate Domain Lifecycle from Agent Execution

Project and task lifecycle must remain independent of the selected coding agent.

The following belong to the platform domain core:

```text
project discovery
project import/init/sync/export

task start
task complete
task abort
task merge

sequential lifecycle
parallel/worktree lifecycle

dependency semantics
locking/concurrency

Git bundle handoff
repository integrity checks
```

None of these modules should import Codex-, Cursor-, Antigravity-, or Copilot-specific code.

---

## 2.3 Platform Policy Is Not Provider Configuration

The platform must stop treating files such as:

```text
Codex config.toml
Cursor cli-config.json
Cursor sandbox.json
provider-specific CLI flags
```

as the canonical security policy.

Instead:

```text
Platform Execution Policy
          |
          v
Provider Policy Adapter
          |
          v
Native provider configuration
```

Provider configuration becomes generated enforcement material.

The platform policy becomes the source of truth.

---

## 2.4 Capability-Based Integration

Agents must not be assumed to have equivalent security or execution features.

Each provider driver declares capabilities.

Example:

```text
noninteractive_execution
interactive_pty
structured_output
session_resume

filesystem_sandbox
filesystem_deny
workspace_readonly
workspace_write

network_deny
network_allowlist

environment_filtering
provider_state_protection

native_command_permissions
native_approval_controls

mcp_support
hooks_support
```

Execution profiles declare required capabilities.

The broker resolves:

```text
requested profile
       +
agent capabilities
       =
supported execution plan
```

If the agent cannot satisfy a required capability, execution fails closed.

The broker must never silently downgrade a hardened execution profile.

---

# 3. Target Architecture

```text
                              agentctl
                                 |
                          stable broker RPC
                                 |
                                 v
                         +---------------+
                         |    agentd     |
                         |    Broker     |
                         +-------+-------+
                                 |
       +-------------------------+-------------------------+
       |                         |                         |
       v                         v                         v
+--------------+         +---------------+          +--------------+
| Domain Core  |         | Policy Engine |          | Agent        |
|              |         |               |          | Registry     |
| projects     |         | profiles      |          +------+-------+
| tasks        |         | resolver      |                 |
| dependencies |         | capabilities  |       +---------+---------+
| Git          |         | validation    |       |         |         |
| locking      |         +-------+-------+       v         v         v
+------+-------+                 |           Codex     Cursor      ...
       |                         |           Driver    Driver
       |                         |                         |
       +-------------------------+-------------------------+
                                 |
                                 v
                       ResolvedExecutionPlan
                                 |
                                 v
                        +----------------+
                        | Runtime Backend|
                        +-------+--------+
                                |
                                v
                          PodmanBackend
                                |
                  +-------------+-------------+
                  |                           |
                  v                           v
            Outer Isolation            Provider-Native
                                          Sandbox
```

---

# 4. Core Modules

## 4.1 Broker

`agentd` remains the trusted broker process.

Its responsibilities should be reduced to orchestration:

```text
validate RPC
resolve project/task
resolve execution profile
resolve agent
verify capabilities
produce execution plan
invoke runtime backend
record run result
```

It should not contain logic such as:

```python
if provider == "codex":
    ...
elif provider == "cursor":
    ...
```

except inside provider driver implementations.

---

## 4.2 Domain Core

Suggested modules:

```text
core/
    projects.py
    tasks.py
    dependencies.py
    locking.py
    git_handoff.py
    worktrees.py
    models.py
```

The domain core owns state transitions and invariants.

Example:

```text
Task lifecycle

pending
   |
   v
active
   |
   +------> aborted
   |
   v
completed
   |
   v
integrated
```

Provider execution must not directly modify task state.

---

# 5. Agent Driver Layer

## 5.1 AgentDriver Contract

Every supported coding agent implements a common interface.

Conceptually:

```python
class AgentDriver:
    id()
    display_name()

    capabilities()

    installation_spec()
    state_spec()

    version_probe()

    auth_spec()
    auth_status()

    compile_policy(policy)

    create_run_spec(context, policy, prompt)
```

The driver does not start containers itself.

It returns declarative specifications to the broker/runtime layer.

---

## 5.2 Driver Responsibilities

A driver owns:

```text
CLI invocation syntax
provider state layout
authentication lifecycle
version detection
native sandbox configuration
provider-specific policy generation
environment requirements
provider-specific command-line flags
provider-specific compatibility checks
```

A driver must not own:

```text
project lifecycle
Git lifecycle
dependency semantics
locking
worktree management
host mount selection
Podman invocation
project authorization
```

---

## 5.3 Initial Drivers

The target registry is:

```text
CodexDriver
CursorDriver
AntigravityDriver
CopilotDriver
```

Codex and Cursor become the two reference implementations.

Antigravity and Copilot are added only after the driver contract is proven by extracting the existing Codex/Cursor implementation.

---

# 6. Runtime Backend Layer

Agent and runtime are separate concepts.

For v0.2:

```text
RuntimeBackend
    |
    +-- PodmanBackend
```

Future designs may add:

```text
RemoteBackend
CloudAgentBackend
VMBackend
```

but they are explicitly outside the initial v0.2 implementation scope.

The runtime backend owns:

```text
container creation
mounts
namespaces
network namespace
resource limits
environment injection
read-only/read-write workspace mount
image execution
process lifetime
PTY plumbing
stdout/stderr
termination
```

It does not understand provider CLI semantics.

---

# 7. Provider Registry

The broker should discover supported in-tree drivers through a trusted registry.

Example:

```text
agents/
    base.py
    registry.py

    codex/
        driver.py
        provider.yaml
        Containerfile

    cursor/
        driver.py
        provider.yaml
        Containerfile

    antigravity/
        driver.py
        provider.yaml
        Containerfile

    copilot/
        driver.py
        provider.yaml
        Containerfile
```

The registry is root-owned and deployed with the platform.

v0.2 should not support arbitrary user-downloadable Python plugins.

Modularity means:

> Adding another trusted provider module should not require modifying the broker domain core.

It does not mean:

> Arbitrary external code may be dynamically loaded into `agentd`.

---

# 8. Provider Manifest

A declarative provider manifest may describe static information:

```yaml
id: codex

display_name: OpenAI Codex

driver:
  class: CodexDriver

image:
  containerfile: Containerfile
  tag: localhost/agent-dev/codex

state:
  volume: agent-dev-codex-state
  target: /root/.codex
```

Dynamic security capability declarations should still be verified by driver tests rather than trusted merely because they appear in YAML.

---

# 9. Execution Policy Model

## 9.1 Provider-Neutral Policy

A resolved execution policy should describe capabilities rather than provider settings.

Example:

```yaml
version: 1

workspace:
  access: write

reference:
  access: read

filesystem:
  external: deny

network:
  task_shell:
    mode: deny

credentials:
  provider_auth:
    task_shell: deny

git:
  read: true
  commit: true
  push: false

sandbox:
  required: true

resources:
  cpu: 4
  memory: 8g
  pids: 1024

security_class: hardened
```

For the initial `MA2-POL-001` schema, `task_shell` is the canonical execution-
plane name for both task networking and provider-auth visibility. The schema
uses the following provider-neutral value vocabulary:

```text
workspace.access                  none | read | write
reference.access                  none | read
filesystem.external               deny | read | write
network.task_shell.mode           deny | allowlist | allow
credentials.provider_auth.task_shell
                                  deny | allow
security_class                    hardened | compatibility
```

`network.task_shell.destinations` is meaningful only for `allowlist` mode and
is normalized into deterministic ordering. Destination syntax and hardened
egress enforcement remain later policy/security concerns rather than being
claimed by this schema.

Policy resource limits are normalized before policy resolution. `cpu` must be a
positive finite number and `pids` a positive integer. `memory` accepts either a
positive integer byte count or a positive integer quantity using `k`, `m`, `g`,
or `t` suffixes (case-insensitive); suffixes use 1024-based units. For example,
`8g` and `8192m` normalize to the same integer byte count. Zero, negative,
fractional memory quantities, and arbitrary strings are invalid. This policy-
level normalization does not change the existing runtime `ResourceLimits.memory`
representation established by Phase 3.

---

## 9.2 Policy Hierarchy

Recommended hierarchy:

```text
Platform Baseline
       |
       v
Project Policy
       |
       v
Execution Profile
       |
       v
Run Restrictions
```

Policy composition must be monotonic.

The platform baseline is one complete `ExecutionPolicy`. Project policy,
execution profile, and run restrictions are sparse restriction layers that use
the same nested field names. Omitted fields inherit the current effective
value; supplied fields are normalized through the `ExecutionPolicy` schema
before monotonic validation.

The merge order and per-field narrowing rules are:

```text
workspace.access                    none <= read <= write
reference.access                    none <= read
filesystem.external                 deny <= read <= write
network.task_shell.mode             deny <= allowlist <= allow
credentials.provider_auth.task_shell
                                    deny <= allow
Git read/commit/push                 false <= true
sandbox.required                    false <= true  (true is stricter)
resources cpu/memory/pids           smaller positive ceiling is stricter
security_class                      compatibility <= hardened
```

For `network.task_shell` allowlists, a lower allowlist must be a subset of the
effective upper allowlist. Moving from `allowlist` to `deny` clears inherited
destinations. Moving from unrestricted `allow` to `allowlist` requires explicit
destinations.

A lower level may restrict rights or demand stronger isolation, but it must not
widen an effective upper-layer restriction. Contradictory widening is rejected
rather than silently clamped. For example:

```text
platform:
    task_shell_network = deny

project:
    task_shell_network = allow
```

is invalid. The same rule applies at every boundary in the hierarchy, so a
profile or run restriction cannot re-open a denial introduced by platform or
project policy.

---

# 10. Execution Profiles

The initial system exposes four trusted broker-owned profiles. Profiles are
materialized as sparse `ExecutionPolicy` restriction layers and resolved by the
monotonic `PolicyResolver`. Fields not stated by a profile inherit the current
effective value from the platform/project layers.

`review`, `implement`, and `dependency` do not select a security class; the
effective `security_class` is inherited from platform/project policy or may be
strengthened by later restrictions. The explicit `compatibility` profile is the
legacy-compatibility bundle and pins `security_class=compatibility`. Therefore a
platform/project requirement for `hardened` rejects the compatibility profile
rather than being silently downgraded.

## 10.1 Review

```text
workspace        read-only
reference        read-only
task network     denied
sandbox          required
Git commit       denied
provider secrets hidden from task shell
security class   inherited
```

---

## 10.2 Implement

```text
workspace        read-write
reference        read-only
task network     denied
sandbox          required
Git read         allowed
Git commit       allowed
Git push         denied
provider secrets hidden from task shell
security class   inherited
```

---

## 10.3 Dependency

Used only when implementation requires external package retrieval.

```text
workspace        read-write
sandbox          required

task network:
    explicit destination allowlist supplied for the run

Git commit       allowed
Git push         denied

provider credentials:
    hidden from task shell

security class   inherited
```

The built-in dependency profile does not hard-code package registries. It must
be materialized with at least one explicit task-shell destination, and normal
policy resolution still rejects any destination or network mode wider than an
upper-layer restriction.

---

## 10.4 Compatibility

Compatibility replaces the conceptual role currently served by options such as
Codex `--outer-only`.

The built-in compatibility profile pins only:

```text
security_class               compatibility
```

It does not actively relax sandbox, credential, filesystem, Git, or network
controls inherited from upper layers. Those controls may therefore remain
stricter than the minimum compatibility semantics. What compatibility does not
claim is the hardened guarantee:

```text
outer Podman isolation         required by the runtime boundary
provider-native sandbox        not guaranteed by the profile
credential confidentiality    not guaranteed by the security class
task-shell egress isolation    provider/capability dependent
```

Compatibility runs must be clearly marked as weaker than hardened runs. A
hardened upper-layer requirement cannot be changed to compatibility by applying
this profile.

---

# 11. Security Classes

Profiles and security classes should be distinct concepts.

Example:

```text
profile = review
security_class = hardened
```

versus:

```text
profile = review
security_class = compatibility
```

A project may require:

```yaml
minimum_security_class: hardened
```

In this case an unavailable provider sandbox results in execution failure.

No automatic fallback to compatibility mode is permitted.

---

# 12. Network Policy

Network policy should distinguish execution planes.

```yaml
network:

  provider_control_plane:
    mode: provider_required

  task_shell:
    mode: deny

  mcp:
    mode: deny

  web_tools:
    mode: provider_policy
```

For dependency tasks:

```yaml
network:
  task_shell:
    mode: allowlist
    destinations:
      - registry.npmjs.org
      - pypi.org
```

The important security invariant is:

> Provider API connectivity must not imply unrestricted network connectivity for model-generated local commands.

---

# 13. Credential Policy

Credential policy must distinguish the provider control process from model-generated task execution.

```yaml
credentials:

  provider_auth:
    control_plane: allow
    task_shell: deny

  git_auth:
    task_shell: deny

  host_credentials:
    executor: absent

  project_secrets:
    task_shell: deny
```

Preferred enforcement order:

```text
1. secret physically absent from task execution environment
2. namespace or mount separation
3. provider-native filesystem denial
4. environment filtering
```

The current provider-state container volume model may remain during v0.2, but hardened profiles must prove that task subprocesses cannot retrieve the authentication material.

---

# 14. ResolvedExecutionPlan

The broker should resolve all policy before starting Podman.

Example structure:

```text
ResolvedExecutionPlan

agent
agent version constraint
image

workspace mount
reference mounts
state mounts

runtime environment

task network policy
filesystem policy
credential policy

provider-native policy artifacts

resource limits

security class
expected capabilities
```

After this object exists, container execution becomes a deterministic runtime operation.

---

# 15. Run as a Domain Object

A Task and an Agent Run are different concepts.

Example:

```text
REQ-017

Run 001
    Cursor
    implement

Run 002
    Codex
    review

Run 003
    Cursor
    implement

Run 004
    Copilot
    review
```

Introduce persistent run metadata.

Example:

```json
{
  "run_id": "REQ-017-004",
  "task_id": "REQ-017",
  "agent": "copilot",
  "profile": "review",
  "security_class": "hardened",
  "image_id": "...",
  "agent_version": "...",
  "policy_hash": "...",
  "base_commit": "...",
  "result_commit": "...",
  "exit_code": 0
}
```

This is provenance and audit metadata.

It must not evolve into a generic scheduler.

---

# 16. CLI Direction

Current provider implementation details should gradually disappear from the public CLI.

Preferred syntax:

```bash
agentctl run question-manager REQ-001 \
    --agent cursor \
    --profile implement \
    "Implement REQ-001."
```

Review:

```bash
agentctl run question-manager REQ-001 \
    --agent codex \
    --profile review \
    "Review REQ-001."
```

Agent discovery:

```bash
agentctl agents
```

Example result:

```text
codex          ready
cursor         ready
antigravity    unauthenticated
copilot        unavailable
```

`agentctl` should not have a compile-time list such as:

```text
["codex", "cursor"]
```

The authoritative list comes from the broker registry.

---

# 17. Testing Architecture

## 17.1 Provider-Neutral Contract Tests

Security and execution acceptance must be defined in terms of observable capabilities.

Example hardened implementation contract:

```text
CAN:
    read workspace
    modify workspace
    run tests
    create Git commit

CANNOT:
    read human checkout
    read provider credentials
    read host credentials
    write outside permitted workspace
    access arbitrary Internet destinations
    access private host networks
    obtain a container runtime socket
```

The same contract suite should execute against every provider claiming support for that profile.

---

## 17.2 Provider-Specific Tests

Provider-specific test suites should be restricted primarily to:

```text
installation
version detection
authentication
state location
config serialization
native sandbox translation
CLI invocation
output parsing
session behavior
```

They must not redefine the platform security contract.

---

# 18. Source Tree: Current Implemented Layout and Target

The implemented package layout after `MA2-RT-005` is:

```text
platform-src/
├── agentdev/
│   ├── agents/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── state.py
│   │   ├── codex.py
│   │   └── cursor.py
│   ├── broker/
│   │   ├── cli.py
│   │   ├── daemon.py
│   │   ├── rpc.py
│   │   └── runtime_io.py
│   ├── core/
│   │   ├── models.py
│   │   ├── validation.py
│   │   ├── projects.py
│   │   ├── git_handoff.py
│   │   ├── tasks.py
│   │   ├── dependencies.py
│   │   ├── worktrees.py
│   │   └── locking.py
│   ├── execution/
│   │   └── plan.py
│   ├── policy/
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── resolver.py
│   │   └── profiles.py
│   └── runtime/
│       ├── base.py
│       └── podman.py
└── bin/
    ├── agentctl
    └── agentd
```

`platform-src/bin/agentd` and `platform-src/bin/agentctl` remain thin
compatibility entrypoints.

At this checkpoint:

```text
core/*
    owns provider-neutral project/task/Git/worktree/dependency/locking behavior

agents/*
    owns the AgentDriver contract, trusted registry, provider state semantics,
    and concrete Codex/Cursor provider behavior

execution/plan.py
    owns the broker-authorized ResolvedExecutionPlan boundary

runtime/*
    owns provider-neutral execution and concrete Podman translation/process
    control without provider CLI semantics

broker/runtime_io.py
    adapts broker RPC frames to the provider-neutral runtime I/O contract

broker/daemon.py
    authorizes project/task paths, resolves run plans, orchestrates provider
    control-plane operations, and retains separate maintenance Podman operations

policy/
    owns the normalized ExecutionPolicy schema, monotonic PolicyResolver, and
    trusted built-in execution profiles
```

`agents`, `execution`, and `runtime` are implemented subsystems. Policy work is
implemented through `MA2-POL-003`: schema, resolver, and the four built-in
profiles are concrete package modules. Capability matching, provider policy
compilers, legacy mapping, and policy hashing remain later Phase 4 requirements.

The broader target source tree remains:

```text
platform-src/
└── agentdev/

    broker/
        server.py
        rpc.py

    core/
        models.py
        projects.py
        tasks.py
        dependencies.py
        locking.py
        git_handoff.py
        worktrees.py

    execution/
        service.py
        plan.py
        runs.py

    policy/
        schema.py
        resolver.py
        capabilities.py

        profiles/
            review.yaml
            implement.yaml
            dependency.yaml
            compatibility.yaml

    runtime/
        base.py
        podman.py

    agents/
        base.py
        registry.py

        codex/
            driver.py
            provider.yaml
            Containerfile

        cursor/
            driver.py
            provider.yaml
            Containerfile

        antigravity/
            driver.py
            provider.yaml
            Containerfile

        copilot/
            driver.py
            provider.yaml
            Containerfile
```

The system remains one broker daemon rather than being split into
microservices.

---

# 19. Migration Roadmap

## Phase 0 — Baseline Freeze

Status: **Complete**

### Objective

Create a stable v0.1 behavioral baseline before structural refactoring.

### Work

- run and record the existing package acceptance suite;
- fix any tests that no longer match the current implementation;
- record Codex and Cursor sequential E2E;
- record parallel/worktree E2E;
- record cross-provider review E2E;
- capture the current executor security audit;
- define the current RPC compatibility baseline.

### Exit Criteria

```text
v0.1 behavior is reproducible
all known deviations are documented
no architecture migration begins on an unstable baseline
```

---

# Phase 1 — Mechanical Broker Modularization

Status: **Complete**

## Objective

Split the current `agentd` implementation into internal modules without changing externally observable behavior.

## Implemented Work

Extracted:

```text
shared validation and domain models
projects
Git handoff
tasks
dependencies
worktrees
locking
RPC validation, framing, dispatch, and socket serving
```

Introduced provider-neutral internal models including:

```text
ProjectContext
TaskContext
ExecutorSpec
ProviderStateSpec
```

Kept Codex/Cursor invocation behavior unchanged.

Podman/runtime extraction is intentionally deferred to Phase 3. Provider-specific
execution remains in the broker daemon until the AgentDriver and runtime
boundaries are implemented.

### Important Constraint

No new provider and no new policy semantics are introduced in this phase.

### Exit Criteria

Existing v0.1 acceptance tests pass unchanged or with purely structural test adjustments.

This exit criterion is satisfied by the frozen pre-CORE characterization tests
plus the CORE-specific deterministic regressions.

---

# Phase 2 — AgentDriver Contract

Status: **Complete — MA2-DRV-001 through MA2-DRV-006**

## Objective

Remove Codex/Cursor branching from the broker core.

## Work

Introduce:

```text
AgentDriver
AgentRegistry
AgentCapabilities
RunSpec
```

Move into `CodexDriver`:

```text
state path
auth
auth status
version probe
Codex command construction
Codex configuration handling
outer-only implementation details
```

Move into `CursorDriver`:

```text
state path
auth
auth status
version probe
Cursor command construction
Cursor configuration reconciliation
```

Replace:

```text
ALLOWED_PROVIDERS
```

with the trusted agent registry.

### Implementation outcome

`MA2-DRV-001` through `MA2-DRV-006` are complete:

- the provider-neutral `AgentDriver`, capability, state, authentication,
  version, installation, policy-artifact, and run-spec contracts are defined;
- the broker resolves providers through a fixed trusted in-tree `AgentRegistry`;
- provider state layout, legacy migration metadata, native config targets, and
  Cursor reconciliation metadata are driver-owned;
- `CodexDriver` owns Codex auth/status/version, `codex exec`, sandbox selection,
  outer-only compatibility, and Codex state/config metadata;
- `CursorDriver` owns Cursor auth/status/version, `agent --trust` run
  construction, split state/auth volumes, and `permissions` reconciliation
  metadata;
- the test-only fake-driver regression proves a third provider with a different
  executable, state target, auth command, version probe, and minimal capability
  set can use generic broker state/status/version/auth/run-spec paths without
  modifying production broker, project/task/Git core, or runtime source.

The fake driver is not registered or shipped as a production provider.

The broker no longer needs transitional provider-command fallbacks for Codex or
Cursor. Podman construction, resource/network/mount enforcement, process/PTY
handling, and generic state-migration execution remain broker/runtime concerns
for Phase 3.

Next: `MA2-RT-001` defines `ResolvedExecutionPlan`, the validated boundary
between driver-produced provider semantics and runtime execution.

### Exit Criteria

Adding another test driver does not require modifying broker execution logic.

Codex/Cursor E2E continues to pass.

---

# Phase 3 — Runtime Backend Extraction

Status: **Complete — MA2-RT-001 through MA2-RT-005**

## Objective

Separate agent semantics from Podman execution.

## Work

Introduce:

```text
RuntimeBackend
PodmanBackend
```

Agent drivers produce `RunSpec`.

The broker converts:

```text
RunSpec
+
ResolvedExecutionPolicy
+
ProjectContext
```

into:

```text
ResolvedExecutionPlan
```

`PodmanBackend` executes the plan.

### Implementation outcome

`MA2-RT-001` through `MA2-RT-005` establish the runtime execution boundary:

- `ResolvedExecutionPlan` carries the broker-authorized workspace/reference/task
  mounts, provider state and policy artifacts, environment, resources, network
  requirements, interaction mode, and capability/security placeholders;
- `RuntimeBackend` defines provider-neutral execution and normalized interactive
  control/output contracts;
- `PodmanBackend` translates a resolved plan into the rootless Podman executor
  envelope and owns process lifetime, PTY plumbing, cancellation, cidfile cleanup,
  environment injection, mount translation, resource flags, and network flags;
- `broker/runtime_io.py` translates RPC input/resize/cancel frames into the
  provider-neutral runtime control contract and translates runtime output back to
  broker RPC output frames;
- the shared raw-Podman runtime dispatcher owns interactive/noninteractive
  process selection, PTY setup, stdout streaming, input, resize, cancellation,
  signal escalation, cidfile lifecycle, and container cleanup; provider auth now
  supplies only the driver-declared interaction requirement and timeout;
- optional GitNexus indexing remains a non-agent runtime consumer: the broker
  authorizes the selected task workspace and delegates noninteractive execution
  through the runtime boundary without registering GitNexus as an `AgentDriver`;
- GitNexus repository index data remains in the authorized task workspace, while
  its global registry and LadybugDB runtime state persist in an isolated
  per-task home under `<project>/runtime/gitnexus/<task>/`, mounted as
  `/gitnexus-home:rw` with `HOME=/gitnexus-home`; the container root remains
  read-only;
- a missing per-task GitNexus registry triggers a one-time forced rebuild so a
  persisted repository-local index is not treated as finalized without its
  corresponding registry; later runs retain the normal incremental path.

The optional GitNexus intelligence image remains outside the agent registry. A
failure to build that optional image is still non-fatal for core executors and
is reported as a warning.

#### Known GitNexus limitation and deferred optional work

`agentctl index` remains an offline runtime operation (`network=none`). The
current intelligence image does not pre-install the optional LadybugDB FTS
extension. GitNexus may therefore attempt an extension download, fail because
network access is disabled, and continue with a warning that FTS features are
unavailable. A successful repository analysis without FTS satisfies the RT-005
runtime acceptance criteria; FTS is not part of the current core execution
contract.

TODO (optional): prepare the compatible LadybugDB FTS extension during the
network-enabled intelligence-image/bootstrap stage, seed the extension into new
per-task GitNexus runtime homes, and switch runtime extension handling to a
load-only/offline mode. This enhancement must preserve `network=none` for normal
indexing, the read-only container root, per-task state isolation, and GitNexus's
status as a non-agent runtime consumer. Failure to prepare the optional FTS
extension must remain non-fatal for Codex/Cursor executors and for GitNexus
indexing without FTS.

The broker continues to authorize project paths and construct the resolved plan.
Provider drivers remain declarative and do not invoke Podman. Podman operations
used for image build, provider-state preparation, smoke checks, and other broker
control-plane maintenance remain separate from resolved agent-run execution and
do not reintroduce provider CLI semantics into the runtime backend.

The frozen executor boundary and provider invocation contracts remain unchanged.

### Exit Criteria

No provider driver directly executes Podman.

No Podman runtime module contains provider-specific CLI logic.

Interactive/noninteractive process-control mechanics are runtime-owned rather
than provider-owned.

GitNexus indexing uses the runtime boundary without becoming an `AgentDriver` or
agent-registry entry, and failure of its optional image build remains non-fatal
to core executors.

---

# Phase 4 — Provider-Neutral Policy Model

Status: **In progress — MA2-POL-001 through MA2-POL-003 complete; next MA2-POL-004**

## Objective

Make the broker-owned execution policy the source of security semantics.

## Work

Implement:

```text
ExecutionPolicy
PolicyResolver
ExecutionProfile
SecurityClass
CapabilityRequirement
```

Initial profiles:

```text
review
implement
dependency
compatibility
```

Map legacy options:

```text
--readonly
--outer-only
```

to compatibility aliases where necessary.

Do not immediately remove them.

### Exit Criteria

Codex and Cursor runs are requested using profiles.

Provider adapters translate the same resolved policy into native enforcement mechanisms.

---

# Phase 5 — Security Closure

## Objective

Close the two remaining execution-boundary gaps:

```text
credential confidentiality
destination-level task egress restriction
```

## Credential Work

For each hardened provider driver prove:

```text
provider control process can authenticate

AND

model-generated task subprocess cannot retrieve
provider authentication/session secrets
```

Cover:

```text
environment
filesystem
process inheritance
descriptors where relevant
provider state
generated configuration
logs
```

## Network Work

Enforce:

```text
task-shell network denied by default
```

and, for dependency profiles:

```text
destination allowlist
```

Provider control-plane connectivity remains separate.

### Exit Criteria

A provider may advertise `hardened` only if the common adversarial contract passes.

`compatibility` remains available but is explicitly weaker.

---

# Phase 6 — Run Provenance

## Objective

Make multi-agent task execution auditable.

## Work

Introduce persistent run records containing:

```text
agent
agent version
image ID
profile
security class
policy hash
task
base commit
result commit
exit code
timestamps
```

Add:

```bash
agentctl run-list <project> <requirement>
agentctl run-status ...
```

only if needed for inspection.

### Exit Criteria

A requirement involving multiple agents has a deterministic execution history without adding scheduler semantics.

---

# Phase 7 — Dynamic Agent CLI

## Objective

Remove provider enumeration from `agentctl`.

## Work

Introduce:

```bash
agentctl agents
```

Make:

```text
auth
status
versions
run
```

registry-driven.

Example:

```bash
agentctl auth codex
agentctl auth cursor
agentctl auth copilot
```

No frontend code change should be necessary when a new in-tree driver is enabled.

---

# Phase 8 — Copilot Driver

## Objective

Validate the architecture using the first new provider.

## Why Copilot First

Copilot is a useful third implementation because it tests:

```text
different authentication model
different CLI syntax
different sandbox model
different provider state
different policy translation
```

without requiring changes to the project/task lifecycle.

## Work

Implement:

```text
CopilotDriver
Copilot image
authentication
version/status
noninteractive run
policy adapter
contract tests
```

Initially allow:

```text
compatibility
```

and only advertise:

```text
hardened
```

after the common security acceptance suite passes.

### Exit Criteria

No existing Codex/Cursor/core module requires provider-specific modifications to add Copilot.

This is the main proof that the modular architecture works.

---

# Phase 9 — Antigravity Driver

## Objective

Add the fourth agent using the same extension mechanism.

## Work

Implement:

```text
AntigravityDriver
image
authentication
version/status
noninteractive execution
sandbox translation
capability detection
```

Treat unstable or unsupported provider security features explicitly in capability reporting.

Do not emulate missing hardened capabilities by silently weakening the profile.

### Exit Criteria

Antigravity runs through the same broker/runtime/policy interfaces as the other providers.

---

# Phase 10 — Legacy Interface Cleanup

## Objective

Remove architecture-specific historical interfaces after the new abstraction has proven stable.

Candidates:

```text
provider-specific CLI flags
--outer-only as a top-level generic concept
hard-coded provider choices
old policy seed structure
provider branching in tests
```

Preserve explicit compatibility functionality if operational evidence shows it remains necessary.

---

# Phase 11 — Pilot Review

## Objective

Evaluate whether the architecture requires another isolation layer.

After real multi-agent project usage, reassess whether the following are justified:

```text
separate provider control process / task runner
common outer egress proxy
VM-grade executor boundary
remote execution backend
automatic provider routing
quota-aware routing
cloud agents
```

None of these should be implemented solely because the new architecture makes them possible.

---

# 20. Recommended Implementation Order

The practical sequence is:

```text
0. freeze baseline                                      [complete]
1. modularize agentd                                    [complete]
2. define AgentDriver / AgentCapabilities / RunSpec     [complete]
3. extract CodexDriver                                  [complete]
4. extract CursorDriver                                 [complete]
5. extract RuntimeBackend / PodmanBackend               [complete]

6. define ExecutionPolicy schema                        [complete: MA2-POL-001]
7. add monotonic PolicyResolver                          [complete: MA2-POL-002]
8. add built-in profiles                                 [complete: MA2-POL-003]
9. add capability checks                                  [next: MA2-POL-004]
10. map legacy flags and compile provider-native policy
11. add canonical policy serialization/hash

12. close credential confidentiality
13. close task egress restriction

14. introduce Run records
15. make agentctl registry-driven

16. add CopilotDriver
17. add AntigravityDriver

18. remove legacy provider-specific interfaces
19. pilot
```

The crucial ordering rule is:

> Do not add the third and fourth providers before Codex and Cursor have been successfully extracted behind the new driver contract.

Otherwise the current provider coupling will simply be reproduced in more modules.

---

# 21. Explicit Non-Goals for v0.2

v0.2 must not introduce:

```text
generic job scheduling
distributed execution
arbitrary third-party broker plugins
agent marketplace
automatic model/provider routing
automatic quota routing
general CI/CD
backlog management
automatic human-branch promotion
arbitrary container orchestration
multi-host clustering
```

Potential future extension points may exist, but their implementations remain out of scope.

---

# 22. Target v0.2 Completion State

v0.2 is complete when:

```text
Domain lifecycle                 provider-neutral
Git lifecycle                    provider-neutral
Runtime execution                backend abstraction
Security policy                  provider-neutral
Provider configuration           generated adapter output
Agent list                       registry-driven
Codex                            supported
Cursor                           supported
Copilot                          supported
Antigravity                      supported or explicitly capability-limited

Credential confidentiality       closed for hardened profiles
Task egress restriction          closed for hardened profiles

Compatibility mode               explicit and weaker
Silent security downgrade        impossible

Existing sequential lifecycle    preserved
Existing parallel lifecycle      preserved
Existing Git trust boundary      preserved
```

At this point the platform becomes a modular local multi-agent development environment without becoming a generic orchestration framework.

---

# 23. Architectural Decision Summary

The v0.2 architecture adopts the following decisions:

1. `agentd` remains the trusted broker.
2. Rootless Podman remains the initial runtime boundary.
3. Existing Git/task lifecycle is preserved.
4. Provider-specific logic moves behind `AgentDriver`.
5. Container execution moves behind `RuntimeBackend`.
6. Security semantics move into a provider-neutral `ExecutionPolicy`.
7. Agents declare capabilities rather than being assumed equivalent.
8. Hardened profiles fail closed.
9. Provider-native sandboxes are used as enforcement mechanisms behind the common policy model.
10. Compatibility execution is explicit and is not security-equivalent to hardened execution.
11. Run provenance becomes a first-class record for multi-agent workflows.
12. Codex and Cursor are the reference drivers.
13. Copilot is the recommended first validation of extensibility.
14. Antigravity follows after the driver architecture has been proven.
15. Additional infrastructure is introduced only when pilot evidence requires it.