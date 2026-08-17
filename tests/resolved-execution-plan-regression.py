#!/usr/bin/env python3
"""Regression coverage for the resolved executor-plan boundary."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_SRC = ROOT / "platform-src"
sys.path.insert(0, str(PLATFORM_SRC))

from agentdev.agents.base import (  # noqa: E402
    AgentCapabilities,
    AgentDriver,
    AuthSpec,
    InstallationSpec,
    PolicyFileSpec,
    ProviderPolicyArtifacts,
    RunSpec,
    VersionProbeSpec,
)
from agentdev.agents.registry import AgentRegistry  # noqa: E402
from agentdev.core.models import ProviderStateSpec, TaskContext  # noqa: E402
from agentdev.execution.plan import (  # noqa: E402
    ExecutionMount,
    NetworkRuntimeRequirements,
    ResolvedExecutionPlan,
    ResolvedProviderPolicyArtifacts,
    ResourceLimits,
)


def load_agentd():
    path = ROOT / "platform-src/bin/agentd"
    loader = importlib.machinery.SourceFileLoader("agentd_rt001_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


agentd = load_agentd()


class PlanDriver(AgentDriver):
    def id(self) -> str:
        return "plan-test"

    def display_name(self) -> str:
        return "Plan Test"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            workspace_modes=frozenset({"readonly", "writable"}),
            interactive_run=True,
        )

    def state_spec(self) -> tuple[ProviderStateSpec, ...]:
        return (ProviderStateSpec("plan-state", "/var/lib/plan-test"),)

    def installation_spec(self) -> InstallationSpec:
        return InstallationSpec("plan-test", "Containerfile.plan-test")

    def version_probe(self) -> VersionProbeSpec:
        return VersionProbeSpec(("planctl", "--version"))

    def auth_spec(self) -> AuthSpec:
        return AuthSpec(("planctl", "login"))

    def auth_status_spec(self) -> RunSpec:
        return RunSpec(("planctl", "status"), interactive=False)

    def compile_policy(self, policy: object) -> ProviderPolicyArtifacts:
        return ProviderPolicyArtifacts(
            files=(PolicyFileSpec("/trusted/plan-policy", "/etc/plan/policy", True),),
            argv=("--policy", "workspace"),
            environment=(("PLAN_POLICY", "workspace"),),
        )

    def create_run_spec(
        self,
        context: TaskContext,
        policy: ProviderPolicyArtifacts,
        prompt: str,
    ) -> RunSpec:
        return RunSpec(
            ("planctl", "run", *policy.argv, prompt),
            environment=(("PLAN_DRIVER", "1"),),
            interactive=True,
            policy_artifacts=policy,
        )


def mount(source: str, target: str, ro: bool, role: str) -> ExecutionMount:
    return ExecutionMount(source, target, ro, role)


def sample_plan(*, readonly: bool = False) -> ResolvedExecutionPlan:
    return ResolvedExecutionPlan(
        agent_id="plan-test",
        image="plan-image",
        argv=("planctl", "run"),
        environment=(("AGENT_TASK_ID", "REQ-1"),),
        workspace_mount=mount("/host/workspace", "/workspace", readonly, "workspace"),
        reference_mounts=(mount("/host/reference", "/reference", True, "reference"),),
        task_metadata_mount=mount(
            "/host/tasks/REQ-1.json", "/task/metadata.json", True, "task-metadata"
        ),
        provider_state_mounts=(
            mount("plan-state", "/var/lib/plan-test", False, "provider-state"),
        ),
        provider_policy_artifacts=ResolvedProviderPolicyArtifacts(),
        resource_limits=ResourceLimits(256, "4g", 2),
        network=NetworkRuntimeRequirements("slirp4netns:allow_host_loopback=false"),
        readonly=readonly,
        interaction_mode="interactive",
        required_capabilities=frozenset({
            "workspace:readonly" if readonly else "workspace:writable",
            "interactive-run",
        }),
    )


def check_model_validation() -> None:
    plan = sample_plan()
    assert plan.as_dict()["agent_id"] == "plan-test"
    assert plan.all_mounts()[0].target == "/workspace"

    try:
        ResolvedExecutionPlan(
            **{
                **{name: getattr(plan, name) for name in plan.__dataclass_fields__},
                "readonly": True,
            }
        )
    except ValueError as exc:
        assert "contradicts" in str(exc)
    else:
        raise AssertionError("contradictory workspace/read-only modes were accepted")

    try:
        ResolvedExecutionPlan(
            **{
                **{name: getattr(plan, name) for name in plan.__dataclass_fields__},
                "reference_mounts": (
                    mount("/host/reference", "/reference", False, "reference"),
                ),
            }
        )
    except ValueError:
        pass
    else:
        raise AssertionError("writable reference mount was accepted")

    try:
        ResolvedExecutionPlan(
            **{
                **{name: getattr(plan, name) for name in plan.__dataclass_fields__},
                "provider_state_mounts": (
                    mount("plan-state", "/workspace", False, "provider-state"),
                ),
            }
        )
    except ValueError as exc:
        assert "contradictory mounts" in str(exc)
    else:
        raise AssertionError("conflicting mount targets were accepted")

    for invalid in (
        lambda: ResourceLimits(0, "4g", 2),
        lambda: NetworkRuntimeRequirements(""),
        lambda: mount("state", "relative", False, "provider-state"),
    ):
        try:
            invalid()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid execution-plan field was accepted")


def check_broker_plan_resolution() -> None:
    original_registry = agentd.AGENT_REGISTRY
    try:
        registry = AgentRegistry((PlanDriver(),)).freeze()
        agentd.AGENT_REGISTRY = registry

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "projects/demo"
            workspace = project / "repo/agent"
            reference = project / "reference"
            tasks = project / "tasks"
            git_common = workspace / ".git"
            for path in (workspace, reference, tasks, git_common):
                path.mkdir(parents=True, exist_ok=True)
            meta = tasks / "REQ-1.json"
            meta.write_text("{}\n", encoding="utf-8")

            context = TaskContext(
                project="demo",
                task="REQ-1",
                mode="parallel",
                status="active",
                metadata_path=meta,
                workspace=workspace,
                record={"base_commit": "0123456789abcdef", "mode": "parallel"},
            )
            driver = registry.get("plan-test")
            policy = driver.compile_policy({"readonly": True})
            run_spec = driver.create_run_spec(context, policy, "review")
            cfg = {
                "root": str(root),
                "images": {"plan-test": "plan-test-image"},
                "limits": {"pids": 128, "memory": "2g", "cpus": 1},
            }

            plan = agentd.create_run_execution_plan(
                cfg,
                "plan-test",
                context,
                run_spec,
                readonly=True,
                outer_only=False,
                reference=reference,
                git_common=git_common,
            )

            assert plan.agent_id == "plan-test"
            assert plan.image == "plan-test-image"
            assert plan.argv == ("planctl", "run", "--policy", "workspace", "review")
            assert plan.workspace_mount == mount(str(workspace), "/workspace", True, "workspace")
            assert plan.reference_mounts == (
                mount(str(reference), "/reference", True, "reference"),
            )
            assert plan.task_metadata_mount.target == "/task/metadata.json"
            assert plan.provider_state_mounts == (
                mount("plan-state", "/var/lib/plan-test", False, "provider-state"),
            )
            assert plan.provider_policy_artifacts.mounts == (
                mount("/trusted/plan-policy", "/etc/plan/policy", True, "provider-policy"),
            )
            assert plan.provider_policy_artifacts.argv == ("--policy", "workspace")
            assert plan.resource_limits == ResourceLimits(128, "2g", 1)
            assert plan.network.mode == agentd.PROVIDER_NETWORK_MODE
            assert plan.network.http_proxy is False
            assert plan.readonly is True
            assert plan.interaction_mode == "interactive"
            assert plan.security_class is None
            assert "workspace:readonly" in plan.required_capabilities
            assert "interactive-run" in plan.required_capabilities
            assert plan.environment == (
                ("AGENT_TASK_ID", "REQ-1"),
                ("AGENT_TASK_MODE", "parallel"),
                ("AGENT_TASK_BASE_COMMIT", "0123456789abcdef"),
                ("PLAN_DRIVER", "1"),
                ("PLAN_POLICY", "workspace"),
            )
            assert {item.role for item in plan.auxiliary_mounts} == {
                "git-worktree", "git-common"
            }

            argv = agentd.execution_plan_argv(plan)
            assert argv[:3] == ["podman", "run", "--rm"]
            assert f"--network={agentd.PROVIDER_NETWORK_MODE}" in argv
            assert "--http-proxy=false" in argv
            assert "--pids-limit=128" in argv
            assert "--memory=2g" in argv
            assert "--cpus=1" in argv
            assert f"{workspace}:/workspace:ro" in argv
            assert "plan-state:/var/lib/plan-test:rw" in argv
            assert "/trusted/plan-policy:/etc/plan/policy:ro" in argv
            assert "AGENT_TASK_ID=REQ-1" in argv
            assert "PLAN_DRIVER=1" in argv
            assert "PLAN_POLICY=workspace" in argv
            assert argv[-6:] == [
                "plan-test-image", "planctl", "run", "--policy", "workspace", "review"
            ]
    finally:
        agentd.AGENT_REGISTRY = original_registry


def check_source_boundary() -> None:
    daemon_source = (PLATFORM_SRC / "agentdev/broker/daemon.py").read_text(encoding="utf-8")
    plan_source = (PLATFORM_SRC / "agentdev/execution/plan.py").read_text(encoding="utf-8")

    assert "create_run_execution_plan(" in daemon_source
    assert "plan = create_run_execution_plan(" in daemon_source
    assert "argv = execution_plan_argv(plan)" in daemon_source
    assert '"podman"' not in plan_source
    assert "subprocess" not in plan_source
    assert "agentdev.agents.codex" not in plan_source
    assert "agentdev.agents.cursor" not in plan_source
    for literal in ("/root/.codex", "/root/.cursor", "/root/.config/cursor"):
        assert literal not in plan_source


def main() -> None:
    check_model_validation()
    check_broker_plan_resolution()
    check_source_boundary()
    print("resolved execution plan regression checks passed")


if __name__ == "__main__":
    main()
