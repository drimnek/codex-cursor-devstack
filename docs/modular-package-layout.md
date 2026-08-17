# Modular Package Layout

Status: **Implemented by MA2-CORE-001**

This document records the first structural step of the v0.2 modularization.
It describes the physical Python package and compatibility-entrypoint boundary
after `MA2-CORE-001`. It does not claim that the later CORE, execution, policy,
runtime, or provider responsibilities have already been extracted.

## Package structure

The platform source tree now contains an importable `agentdev` package:

```text
platform-src/
├── agentdev/
│   ├── __init__.py
│   ├── agents/
│   │   └── __init__.py
│   ├── broker/
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   └── daemon.py
│   ├── core/
│   │   └── __init__.py
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

At this stage, the existing controller and broker implementations live under:

```text
platform-src/agentdev/broker/cli.py
platform-src/agentdev/broker/daemon.py
```

The other package namespaces are intentionally present as structural anchors
for later extraction cases; they should not yet be interpreted as complete
architectural boundaries.

## Entrypoint compatibility

The public executable entrypoints remain:

```text
platform-src/bin/agentctl
platform-src/bin/agentd
```

They are compatibility loaders rather than the authoritative implementation
files.

This distinction is important for tests and source-level audits:

- runtime/compatibility tests may continue to execute or load the public
  entrypoints;
- source-level semantic checks should inspect the packaged implementation files
  under `agentdev/broker`;
- later extraction cases should move responsibilities from `broker` into the
  dedicated package namespaces without changing the public CLI/RPC contract
  unless a backlog case explicitly requires such a change.

The compatibility loader exists to preserve the frozen v0.1 regression behavior
during the mechanical migration. In particular, existing tests that monkeypatch
entrypoint module globals must continue to work until those tests are migrated
to package-level APIs.

## Behavior preserved by CORE-001

`MA2-CORE-001` is a structural change only. The following contracts remain
frozen by the pre-CORE baseline:

- public `agentctl` command-line behavior;
- broker RPC operation and request-field compatibility;
- provider invocation commands and executor envelope;
- task lifecycle and Git trust-boundary semantics;
- deterministic package acceptance;
- currently closed executor-security guarantees;
- explicitly open credential-confidentiality and destination-egress findings.

A change in any of those behaviors is not part of `CORE-001` and should be
handled by a separate backlog item with its own characterization or acceptance
coverage.

## Testing implications

The deterministic acceptance command remains:

```bash
./tests/package-check.sh
```

Relevant modularization regressions include:

```text
tests/modular-package-layout-regression.py
tests/security-regression.py
tests/broker-rpc-contract-regression.py
tests/provider-invocation-regression.py
tests/executor-security-baseline.py
```

Source-level tests must follow the implementation location rather than assuming
that `platform-src/bin/agentctl` or `platform-src/bin/agentd` contains the full
implementation.

For example:

```text
controller semantic source
    -> platform-src/agentdev/broker/cli.py

broker semantic source
    -> platform-src/agentdev/broker/daemon.py

public compatibility entrypoints
    -> platform-src/bin/agentctl
    -> platform-src/bin/agentd
```

## Deployment requirement

Because the executable entrypoints now load Python files outside
`platform-src/bin`, deployment must install the `platform-src/agentdev` package
together with the entrypoints.

A post-bootstrap deployment should therefore contain at least:

```text
/srv/agent-dev/platform/agentdev/broker/cli.py
/srv/agent-dev/platform/agentdev/broker/daemon.py
```

and the normal runtime checks must continue to succeed:

```bash
agentctl ping
agentctl versions
agentctl smoke
agentctl status
```

## Next extraction boundary

`MA2-CORE-002` may now begin extracting shared validation and domain models from
the packaged implementation.

The extraction rule is:

1. preserve the frozen public and behavioral contracts;
2. move shared concepts behind package-level APIs;
3. keep entrypoints thin;
4. keep tests pointed at the authoritative implementation boundary;
5. avoid mixing structural extraction with policy or runtime behavior changes.
