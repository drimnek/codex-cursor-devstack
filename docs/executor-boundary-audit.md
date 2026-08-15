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
- provider state source type and target;
- Codex immutable policy mount;
- Cursor writable active configuration semantics;
- absence of Docker/Podman socket, host-home, broad platform-root, privileged,
  host-namespace, and explicit device mounts;
- explicitly forwarded task environment variables;
- known secret environment names;
- network mode;
- Podman proxy-environment propagation policy.

The source audit reports `PASS`, `WARN`, and `FAIL`.

Current provider access that requires design review is reported as `WARN`,
including a writable provider home mounted at `/root`, provider outbound
network access, and lack of an explicit Podman HTTP proxy propagation setting.

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

### Writable provider home

A named provider volume mounted as `/root:rw` is structurally isolated from the
human host home, but all provider state in that volume is available to
processes running inside the provider executor. This includes any authentication,
configuration, and cache state stored under the provider home.

This warning should be resolved by deciding which provider paths must remain
writable and which credential material must be inaccessible to task shell
processes.

### Provider network

Provider executors require outbound connectivity for model API access, so
`network=none` is not a general solution for real provider runs.

The hardening decision should define the intended outbound network boundary and
whether host-reachable services or other local network paths require additional
restriction.

### Proxy environment propagation

The audit warns unless Podman HTTP proxy propagation is explicitly configured.
The next hardening decision should determine whether provider containers need
host proxy variables. If not, disable their propagation explicitly; if they are
required, document them as part of the executor environment contract.

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
