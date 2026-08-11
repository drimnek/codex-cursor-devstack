# Upstream Sources

Vendor-specific CLI/auth/security behavior can change. Re-check the primary documentation before changing provider flags or policy schemas.

- OpenAI Codex documentation: https://developers.openai.com/codex/
- OpenAI Codex configuration reference: https://developers.openai.com/codex/config-reference
- OpenAI Codex approvals/security: https://developers.openai.com/codex/agent-approvals-security
- Cursor CLI documentation: https://cursor.com/docs/cli/
- Cursor CLI configuration reference: https://cursor.com/docs/cli/reference/configuration
- Cursor CLI permissions reference: https://cursor.com/docs/cli/reference/permissions
- Podman documentation: https://docs.podman.io/
- Git bundle documentation: https://git-scm.com/docs/git-bundle
- Git worktree documentation: https://git-scm.com/docs/git-worktree
- Git clone documentation: https://git-scm.com/docs/git-clone
- GitNexus repository/documentation: https://github.com/abhigyanpatwari/GitNexus

Architecture decisions in this repository — the `agentctl -> agentd` broker, bundle-based trust crossing, sequential requirements on `agent/integration`, and temporary worktrees for parallel requirements — are project decisions, not vendor requirements.
