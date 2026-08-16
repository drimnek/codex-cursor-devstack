# Codex + Cursor Development Stack

A Linux development stack for Cursor CLI and Codex CLI agents with constrained host access to reduce unintended system impact.

Current repository version: **0.1.0 (pre-pilot)**. The deployment is intentionally limited to **Ubuntu 22.04** and uses rootless Podman as the outer execution boundary.

## Goals

- keep the human workstation account, SSH keys, cloud credentials, and canonical project checkout outside agent mounts;
- run Codex CLI and Cursor CLI through one constrained broker instead of exposing a raw Podman socket;
- keep normal operation non-root after the initial host bootstrap;
- support sequential requirement implementation on one integration branch;
- create Git worktrees only when requirements are intentionally executed in parallel;
- preserve an explicit human-controlled promotion step before changes reach the canonical project branch.

This is a **constrained-access development environment**, not a VM-grade security boundary. Codex and Cursor currently share the same host-level `agentdev` trust domain.

## Architecture

```text
human operator (agentdev-ops)
        |
      agentctl
        |
        | constrained JSON/PTY RPC
        v
/run/agent-dev/agentd.sock
        |
      agentd                   UID=agentdev
        |
  rootless Podman
      /        \
 Codex CLI   Cursor CLI
```

The raw Podman API/socket is not exposed to the operator or agent containers. `agentd` accepts a fixed RPC surface and computes allowed images, workspaces, policy mounts, and provider state itself.

## Architecture evolution

The current `0.1.x` implementation supports Codex and Cursor directly and
retains provider-specific execution and policy logic inside the broker.

The proposed `0.2` architecture keeps the existing broker, rootless Podman,
Git trust boundary, and task lifecycle while introducing provider-neutral
execution policies, capability-based agent drivers, and a runtime backend
boundary for Codex, Cursor, Copilot, Antigravity, and future agents.

See [Multi-Agent Architecture v0.2](docs/multi-agent-architecture-v0.2.md).

## Trust boundary for Git

Each project has two persistent checkouts:

```text
repo/main                      human-controlled
    |
    | committed history transferred as Git bundle
    v
repo/agent                     agent-controlled after initialization
    |
    +-- agent/integration
    +-- temporary worktrees only for parallel requirements
```

`repo/agent` is created by `agentd` while running as `agentdev`. Human-side `agentctl` first exports the selected committed history from `repo/main` as an inbound Git bundle; `agentd` verifies that bundle, creates the agent repository, and checks out `agent/integration` from the expected commit. The agent checkout is therefore owned by `agentdev` from creation and is never a local clone of the human checkout.

**Human-side `agentctl` never executes Git inside `repo/agent`.** All Git lifecycle operations for the agent checkout are executed by `agentd` as `agentdev`. History crosses the trust boundary only through controlled Git bundles rather than by opening the other side's `.git` directory. The `repo/` namespace uses split ownership so the human-controlled and agent-controlled checkouts remain separate, while the exchange namespace grants `agentdev` only the traversal/access required for the broker-managed handoff.

## Directory layout

```text
/srv/agent-dev/
├── platform/                         root-owned deployed platform
│   ├── bin/                          agentctl, agentd
│   ├── containers/
│   ├── seed/                         read-only provider policies
│   └── config/                       platform + expected manifest
│
├── state/
│   ├── home/agentdev/                rootless Podman HOME/storage
│   └── runtime/                      build-manifest.lock.json
│
├── projects/
│   └── <project>/
│       ├── project.json
│       ├── repo/
│       │   ├── main/                 HUMAN ONLY
│       │   └── agent/                AGENT CONTROLLED
│       ├── worktrees/                normally empty
│       ├── tasks/                    lifecycle/requirement metadata
│       ├── exchange/
│       │   ├── inbound/              human -> agent Git bundles
│       │   └── outbound/             agent -> human Git bundles
│       ├── reference/                executor read-only material
│       ├── results/
│       └── runtime/                  broker locks
│
├── backups/
└── tmp/
```

Provider authentication/session state is stored in separate rootless Podman volumes:

```text
agent-dev-codex-home
agent-dev-cursor-home
```

Provider defaults are deployed under `/srv/agent-dev/platform/seed`, but provider runtime configuration follows each CLI's requirements. Codex keeps its authoritative `config.toml` mounted read-only. Cursor keeps its active `cli-config.json` writable because the CLI performs atomic rewrites of that file. The broker materializes the complete seed when the active Cursor config is missing and, on later provider use, reconciles only the platform-managed `permissions` field from the deployed seed while preserving other Cursor-managed fields. The outer Podman boundary remains authoritative for host access in both cases.

## Installation

The bootstrap script is the single installation entrypoint. A fresh supported Ubuntu host does not need Ansible preinstalled.

Optionally validate the deployment without applying the platform playbook:

```bash
./bootstrap.sh --check
```

This validates the supported OS, operator identity, and deployment source tree, installs the minimal bootstrap dependency (`ansible-core`, or `ansible` as a fallback) if it is missing, and always runs `ansible-playbook --syntax-check`. It may therefore install bootstrap packages, but it does not apply the platform playbook.

Deploy from the repository/package root:

```bash
./bootstrap.sh
```

The normal bootstrap performs the same preflight, ensures Ansible is available from the Ubuntu repositories, runs the Ansible syntax check, and only then starts the host-changing playbook. The playbook installs rootless Podman prerequisites, creates `agentdev` and `agentdev-ops`, configures subordinate UID/GID ranges, deploys the platform, and enables the systemd socket/broker.

The playbook also configures the rootless Podman environment for `agentdev`:

- Podman events use the `file` backend rather than journald, keeping successful `agentctl` commands free of misleading journald event errors;
- the `agentdev` systemd user manager receives the cgroup controllers required by executor resource limits;
- bootstrap verifies that rootless Podman uses cgroup v2 with the systemd cgroup manager and can see the required `cpu`, `memory`, and `pids` controllers.

Bootstrap fails if these runtime prerequisites are not effective, even if `podman info` itself can otherwise run successfully.

Commands executed as the restricted `agentdev` identity must use an agent-accessible working directory and must not inherit the operator's repository working directory. Bootstrap validation and the deployed broker use `agent_home` for this purpose. The source repository may therefore be located on a user-owned or removable filesystem that `agentdev` cannot traverse; deployment does not require granting the agent identity access to the checkout.

The bootstrap uses elevated privileges only for package installation and host-level configuration. When invoked from an operator account it uses `sudo` internally; when invoked through `sudo`, it preserves the original operator identity for `agentdev-ops` membership. Direct root invocation requires an explicit non-root `CONTROLLER_USER`.

Log out and back in once after the first bootstrap so the operator receives the `agentdev-ops` supplementary group.

Then validate the broker:

```bash
agentctl ping
```

## Build and validate the runtime

```bash
agentctl ping
agentctl build
agentctl versions
agentctl smoke
agentctl status
```

`agentctl versions` must complete without Podman, OCI runtime, or provider-launcher errors. `agentctl smoke` is intentionally quiet on success and returns exit status `0`. Before provider authentication, `agentctl status` may report an unauthenticated provider state, but it must not fail because of container runtime or cgroup errors.

A successful build writes the observed executor image IDs and CLI versions to:

```text
/srv/agent-dev/state/runtime/build-manifest.lock.json
```

GitNexus is an **optional** intelligence image. A GitNexus image build failure does not invalidate the Codex/Cursor core images.

## Authenticate providers

After runtime validation succeeds, authenticate the provider CLIs:

```bash
agentctl auth codex
agentctl auth cursor
agentctl status
```

Provider authentication/session state is stored in the provider-specific mutable volumes described above. Authentication is a separate validation stage from host/runtime isolation.

## Import a project

```bash
agentctl project-import question-manager ~/projects/question-manager
```

This creates the localized human checkout and initializes one persistent agent checkout. `agentctl` transfers the initial committed history through an inbound Git bundle, and `agentd` creates `repo/agent` directly as `agentdev`. There is no clone-per-requirement model and no human-side Git access to the agent checkout.

Alternatively:

```bash
agentctl project-create question-manager
# populate /srv/agent-dev/projects/question-manager/repo/main
agentctl project-init question-manager
```

`project-init` is the one-time repository handoff point. Human-side `agentctl` creates an inbound Git bundle from `repo/main`; `agentd` verifies the bundle and creates `repo/agent` as `agentdev`, checking out `agent/integration` at the recorded source commit. Human-side `agentctl` never runs Git inside `repo/agent`, including during initialization.

`project-list` is a human-side structural discovery command. It lists valid immediate project directories without executing Git in `repo/agent` and reports each project as `ready` or `incomplete`. JSON is the default output. Use `project-list --oneline` for shell-friendly `name:state` output, one project per line. It deliberately does not route discovery through `agentd`, because the `agentdev` identity only has traverse access to the projects namespace and must not gain directory-listing access across projects.

## Sync committed human changes into the agent integration branch

```bash
agentctl project-sync question-manager
```

The human side creates an inbound Git bundle from the configured main branch. `agentd` verifies/fetches it and only fast-forwards `agent/integration` when the histories are compatible. Sync is rejected while tasks are active or parallel results await integration.

A divergent history is not auto-rebased or auto-merged.

## Sequential dependent requirements

Dependent requirements normally execute on the shared integration branch:

```bash
agentctl task-start question-manager REQ-001
agentctl run cursor question-manager REQ-001 \
  "Implement REQ-001 using the project TDD workflow and commit the completed change."
agentctl run --readonly --outer-only codex question-manager REQ-001 \
  "Review REQ-001 against the requirement and tests."
agentctl task-complete question-manager REQ-001

agentctl task-start question-manager REQ-002 --depends-on REQ-001
```

The task boundary is represented by requirement IDs, task metadata, tests, and commits rather than by a branch for every sequential requirement.

## Parallel independent requirements

Create temporary branches/worktrees only for requirements that are intentionally parallelized:

```bash
agentctl task-start question-manager REQ-010 --parallel --depends-on REQ-009
agentctl task-start question-manager REQ-011 --parallel --depends-on REQ-009

agentctl run cursor question-manager REQ-010 "Implement and commit REQ-010."
agentctl run --outer-only codex question-manager REQ-011 "Implement and commit REQ-011."

agentctl task-complete question-manager REQ-010
agentctl task-complete question-manager REQ-011

agentctl task-merge question-manager REQ-010
agentctl task-merge question-manager REQ-011
```

A completed parallel task does **not** satisfy downstream dependencies until it is merged. Before merge, the broker verifies that the task branch/worktree still points at the recorded `head_commit`, then merges that exact commit SHA. Conflicts abort the automated merge.

Discard an unmerged parallel task with:

```bash
agentctl task-abort question-manager REQ-011
```

Sequential integration history is not automatically reset/reverted by the broker.

## Project status and task metadata

```bash
agentctl project-list
agentctl project-list --oneline
agentctl project-status question-manager
agentctl task-list question-manager
```

Git status for the agent repository is produced by `agentd`, not by the human controller.

## Optional GitNexus indexing

If the optional intelligence image built successfully:

```bash
agentctl index question-manager REQ-010
```

Failure or absence of the intelligence image does not block normal Codex/Cursor execution.

## Export agent history for human review/promotion

Create an outbound bundle:

```bash
agentctl project-export question-manager
```

The command returns a bundle path under:

```text
/srv/agent-dev/projects/question-manager/exchange/outbound/
```

The human can fetch that bundle into a review ref in `repo/main`, inspect it with normal Git/mcode tooling, run final checks, and then merge/cherry-pick according to project policy. The bundle carries Git objects/refs, not the agent repository's hooks or local `.git/config`.

## Provider policy model

The outer Podman boundary is authoritative. Provider permissions/sandboxes are defense-in-depth guardrails.

Codex task execution uses `codex exec` so broker-managed runs are non-interactive. The broker sets `approval_policy=never` for task execution and selects the Codex sandbox from the requested execution mode: read-only runs use `read-only`, normal writable runs use `workspace-write`, and `--outer-only` uses `danger-full-access` inside the executor while relying on the outer Podman boundary for isolation.

`--outer-only` is Codex-only and disables the nested Codex OS sandbox; it does not grant additional host access beyond what the broker already exposes to the container. `--readonly --outer-only` is supported: the broker still mounts the task workspace read-only through Podman while Codex runs without the nested Linux sandbox. Anything else exposed inside the executor remains governed by the Podman/container policy rather than by Codex's inner sandbox.

Codex keeps its active policy file mounted read-only. Cursor uses an explicit global CLI configuration with required schema fields and allowlist mode, but its active `cli-config.json` remains writable because Cursor manages that file itself. Before Cursor provider operations, the broker reconciles `permissions` from the deployed seed into the active config using an atomic replacement, preserving Cursor-managed fields outside `permissions`. This makes the deployed seed authoritative for platform policy without making the entire active Cursor config immutable. Auth/session state remains writable in provider-specific volumes.

## Validation

Run the repository checks first:

```bash
./tests/package-check.sh
```

On the target Ubuntu host, deployment validation can be run without applying the platform playbook:

```bash
./bootstrap.sh --check
```

The check path is self-contained: it installs the Ansible bootstrap dependency if needed and always validates the playbook syntax. The normal `./bootstrap.sh` uses the same dependency/bootstrap and validation path, then applies the playbook only after validation succeeds. Users do not need to install or invoke Ansible manually.

They cover:

- Python/Bash/JSON syntax;
- Cursor policy reconciliation, including initial seeding, stale-policy replacement, preservation of non-policy fields, mode `0600`, and atomic temporary-file cleanup;
- broker-side creation and ownership of `repo/agent` from an inbound Git bundle;
- Git bundle synchronization across the human/agent trust boundary;
- sequential + parallel Git behavior;
- no human-side Git access to `repo/agent`, including repository initialization;
- strict RPC field rejection;
- symlink mount-source and project-subroot rejection;
- parallel dependency integration semantics;
- rejection when a parallel branch moves after `task-complete`.

The first real Ubuntu deployment must additionally validate:

```bash
agentctl ping
agentctl build
agentctl versions
agentctl smoke
agentctl status
```

A successful runtime deployment requires:

- rootless Podman event logging to use the `file` backend;
- cgroup v2 with the systemd cgroup manager;
- `cpu`, `memory`, and `pids` controllers to be visible to rootless Podman;
- executor version probes to complete without Podman or OCI runtime errors;
- `agentctl smoke` to return exit status `0`.

Provider authentication is validated separately:

```bash
agentctl auth codex
agentctl auth cursor
agentctl status
```

The first real deployment should verify both supported Codex execution paths under the chosen Podman profile. If the nested Linux sandbox is available, validate normal `read-only` / `workspace-write` execution. If the container profile prevents nested sandbox initialization, validate `--outer-only` and specifically confirm that `--readonly --outer-only` leaves the workspace read-only and does not move the repository HEAD during review.

### Sequential acceptance gate

Before moving from sequential validation to parallel/worktree acceptance, confirm the following on a disposable project without manual filesystem-permission repair, direct provider-container invocation, or manual provider-config edits:

- a fresh `project-import` creates an agent-owned `repo/agent`, and `project-status` succeeds immediately;
- one sequential task can be started, implemented, committed, reviewed cross-provider, and completed with a clean repository;
- Cursor can read the workspace, run allowed Git commands, create a commit in writable mode, and enforce at least one representative deny rule from the reconciled platform policy;
- stale Cursor permissions are automatically reconciled to the deployed seed while non-policy Cursor fields are preserved;
- Codex can run non-interactively for both read-only review and writable execution through the selected container/sandbox mode;
- `project-export` produces a valid Git bundle whose integration ref matches the expected agent result on the human side;
- `./tests/package-check.sh`, `./bootstrap.sh --check`, `agentctl ping`, `agentctl versions`, `agentctl smoke`, and `agentctl status` all pass after the final source-controlled fixes are deployed.

When these checks pass, the sequential pre-pilot acceptance stage is complete; the next functional stage is parallel task/worktree, merge, abort, locking, and dependency-metadata acceptance.

## Current limitations

- Ubuntu 22.04 only.
- Codex and Cursor share one host-level `agentdev` trust domain.
- Projects also share that host agent identity; container mounts provide normal project visibility separation, not separate project UIDs.
- No automatic provider/quota router.
- No arbitrary Docker/Podman socket access from agent containers.
- No automatic conflict resolution or promotion to the human main branch.
- GitNexus is optional and not injected as an MCP dependency into the core executor images.
- Cursor CLI installation is frozen at image build time but is not yet pinned by a vendor-provided immutable installer artifact in this stack.
- `--outer-only` intentionally disables Codex's inner OS sandbox and relies on broker-generated Podman isolation; resources exposed inside that executor are not additionally restricted by the Codex sandbox.

## Scope discipline

Before the first pilot, this repository should remain small: host bootstrap, brokered execution, Git/task lifecycle, provider policy, and regression checks. It should not grow into a generic scheduler, CI system, backlog manager, or arbitrary container orchestrator without evidence from real project usage.

### Cursor image installation layout

The Cursor installer creates the CLI installation tree under `$HOME/.local`, and the `agent` launcher may reference other files in that tree using paths tied to the installation HOME.

The Cursor executor therefore runs the installer with `HOME=/opt/cursor-cli` from the start, producing the final immutable installation directly under:

```text
/opt/cursor-cli/.local/
```

`/usr/local/bin/agent` points to the launcher in that installation. Do not install Cursor under `/root/.local` and relocate the resulting tree afterward, and do not replace the installation with a single copied launcher file. Either approach can break installer-created links or companion-file lookups.
