# Executor Boundary Security Audit

## Purpose

This stage inventories and validates the current outer Podman executor boundary
before changing provider networking, provider state, or credential handling.

The audit is intentionally separate from `tests/package-check.sh` at first.
Current reviewable exposures must remain visible as `WARN` rather than being
silently accepted or immediately breaking the deterministic package suite.

No Cursor or Codex model task is executed by these checks.

## Checks

`tests/executor-boundary-source-audit.py` loads the selected `agentd` source and
constructs representative Codex and Cursor runtime arguments without invoking
Podman. It checks:

- read-only container root filesystem;
- `cap-drop=all`;
- `no-new-privileges`;
- PID, memory, and CPU limits;
- secure `/tmp` and `/run` tmpfs mounts;
- read-only and writable workspace mount semantics;
- read-only reference and task metadata mounts;
- scoped provider state source type and target;
- absence of a persistent whole-home mount at `/root`;
- Codex immutable policy mount;
- Cursor writable active configuration semantics;
- absence of Docker/Podman socket, host-home, broad platform-root, privileged,
  host-namespace, and explicit device mounts;
- explicitly forwarded task environment variables;
- known secret environment names;
- network mode;
- Podman proxy-environment propagation policy.

The source audit reports `PASS`, `WARN`, and `FAIL`.

Current provider access that still requires design review is reported as
`WARN`, including provider credential readability inside the scoped state
directory and unrestricted outbound egress. HARD-01 makes Podman
proxy-environment propagation an explicit invariant. HARD-02 removes persistent
whole-home mounts and keeps only provider-specific state writable. HARD-03
fixes provider execution to `slirp4netns:allow_host_loopback=false` and keeps
non-provider executor runtime offline with `network=none`.

`tests/manual-executor-boundary-audit.sh` validates the deployed host and broker:

- `agentd.service` runs as `agentdev`;
- `agentdev` uses the dedicated primary group and is not in `agentdev-ops`;
- the projects namespace exposes only traverse ACL access to `agentdev`;
- broker ping succeeds;
- the existing broker smoke boundary succeeds;
- provider status commands complete;
- the deployed `agentd` passes the source audit;
- deployed `agentd` matches repository source or reports deployment drift.

## Source-only audit

Run against the repository source:

```bash
python3 tests/executor-boundary-source-audit.py
```

Generate machine-readable inventory:

```bash
python3 tests/executor-boundary-source-audit.py --json \
  > /tmp/executor-boundary.json

jq '.summary, .inventory' /tmp/executor-boundary.json
```

Treat warnings as a non-zero result:

```bash
python3 tests/executor-boundary-source-audit.py --fail-on-warn
```

Exit codes:

- `0`: no hard failures; warnings are allowed unless `--fail-on-warn` is used;
- `1`: at least one hard boundary invariant failed;
- `2`: no hard failures, but warnings exist and `--fail-on-warn` was requested.

Audit the deployed broker source explicitly:

```bash
AGENTD_UNDER_TEST=/srv/agent-dev/platform/bin/agentd \
  python3 tests/executor-boundary-source-audit.py
```

## Live deployed audit

Run after deployment:

```bash
./tests/manual-executor-boundary-audit.sh
```

Expected initial outcome during inventory may be:

```text
EXECUTOR BOUNDARY AUDIT: PASS WITH WARNINGS
```

This is acceptable only while the warnings are being reviewed as part of this
hardening stage.

The live audit does not require a project name and does not create tasks.

## Strict hardening gate

After the selected hardening changes are implemented, use:

```bash
./tests/manual-executor-boundary-audit.sh --strict
```

In strict mode any remaining audit warning makes the command non-zero.

Do not add the live audit to `package-check.sh`: it depends on a deployed
systemd service and host filesystem state.

The source audit can be added to `package-check.sh` after the warning policy is
finalized and the intended boundary is encoded as stable assertions.

## Interpreting initial warnings

### Scoped provider state and credential readability

HARD-02 replaces the legacy whole-home mounts:

```text
agent-dev-codex-home:/root:rw
agent-dev-cursor-home:/root:rw
```

with scoped provider state:

```text
agent-dev-codex-state:/root/.codex:rw
agent-dev-cursor-state:/root/.cursor:rw
```

On first use, the broker creates the new scoped volume and copies only the
legacy `.codex` or `.cursor` subtree from the old whole-home volume. The old
volume is retained unchanged as a rollback source. A layout marker prevents
silent reuse of a partially initialized new state volume.

For Codex, a legacy `config.toml` is not carried forward as active policy; the
platform seed remains overlaid read-only at `/root/.codex/config.toml`.

For Cursor, `cli-config.json` remains writable in the scoped state volume and
platform-managed permissions continue to be reconciled before provider use.

The executor root filesystem remains read-only outside these scoped state
mounts. `agentctl smoke` checks that writing directly under `/root` fails while
a temporary write inside the provider-specific state directory succeeds.

This does **not** make provider credentials confidential from task processes.
Authentication and cache data under `/root/.codex` or `/root/.cursor` are still
readable by processes running as the provider executor identity. That residual
risk remains a `WARN` and is a separate hardening problem.

### Provider network

Provider executors require outbound connectivity for model API access, so
`network=none` is not a general solution for real provider runs. HARD-03 makes
the rootless provider network explicit:

```text
slirp4netns:allow_host_loopback=false
```

The broker smoke verifies that this backend starts and that a listening host
loopback socket cannot be reached through `host.containers.internal`. Executor
runtime without a provider, such as local repository indexing, defaults to
`network=none`.

This does not restrict provider traffic to an allowlist of destinations. General
outbound egress remains a reviewable warning and is a separate hardening
problem.

### Proxy environment propagation

HARD-01 disables Podman's automatic propagation of host proxy environment
variables into executor containers with `--http-proxy=false`. The source audit
treats absence of this explicit policy as `FAIL`.

This does not disable container networking and does not prevent proxy variables
that are deliberately supplied by another mechanism, such as an explicit
`--env` option or an environment value baked into an image. If a future
deployment requires an outbound proxy, add that proxy as a separate explicit
executor contract rather than relying on implicit host-environment inheritance.

## Acceptance for this inventory step

The inventory step is complete when:

```text
source audit executes                         PASS
live broker/host audit executes               PASS
hard boundary violations                      0
reviewable exposures are explicitly listed   PASS
deployment drift is absent or explained       PASS
```

Warnings are not considered resolved merely because the audit exits zero in
non-strict mode. They define the inputs for the following hardening patches.

After HARD-03, the expected review warnings remain limited to two categories
per provider profile:

```text
provider credentials readable
outbound egress
```

The previous `provider home writable` warning must be gone and a persistent
mount at `/root` is a hard failure.
