# Changelog

## Unreleased

### Fixed

- Preserve the full Cursor CLI installer layout under `/opt/cursor-cli`; copying only the `agent` launcher caused its Node entrypoint to lose companion files such as `index.js`.


## 0.1.0 - pre-pilot

First repository release candidate for the Codex + Cursor Linux development stack.

Key characteristics:

- dedicated `agentdev` runtime identity and `agentdev-ops` operator group;
- `agentctl -> agentd -> rootless Podman` execution model;
- persistent agent integration clone with temporary worktrees for parallel requirements;
- Git bundle handoff between human-controlled and agent-controlled repositories;
- Codex CLI and Cursor CLI executor images with constrained host mounts;
- immutable provider policy files separated from mutable authentication/session state;
- optional GitNexus intelligence image;
- task lifecycle metadata, locking, dependency validation, and cross-provider review support.
- self-validating bootstrap flow that installs Ansible when needed and always syntax-validates the playbook before check-only completion or deployment.
- agent-identity bootstrap/runtime commands use an explicit `/srv/agent-dev` working directory and do not require access to the source checkout.

Earlier `1.x`/`2.x`/`3.x` labels used during design were internal iterations and are not repository releases.
