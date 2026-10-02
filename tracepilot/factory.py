"""根据工作区配置组装 TracePilot 的运行时依赖。"""

from __future__ import annotations

from pathlib import Path

from tracepilot.planner import AgenticPatchPlanner, RuleBasedPatchPlanner
from tracepilot.providers import OpenAICompatibleModelCaller
from tracepilot.sandbox import DockerJavacSandboxRunner
from tracepilot.security import WorkspacePolicy
from tracepilot.tools import CodeTools, JavacMainTestRunner, SafePatchApplier
from tracepilot.workflow import RepairWorkflow


def build_offline_workflow(
    workspace_root: str | Path,
    *,
    state_dir: str | Path,
    java_executable: str = "java",
    javac_executable: str = "javac",
    trusted_local_runner: bool = False,
    sandbox_image: str = "eclipse-temurin:17-jdk-jammy",
) -> RepairWorkflow:
    """离线规划器不调用模型；宿主机执行仅限显式信任的样例。"""
    state_root = Path(state_dir)
    state_root.mkdir(parents=True, exist_ok=True)
    policy = WorkspacePolicy(workspace_root)
    tools = CodeTools(policy)
    runner = (
        JavacMainTestRunner(
            workspace_root,
            java_executable=java_executable,
            javac_executable=javac_executable,
        )
        if trusted_local_runner
        else DockerJavacSandboxRunner(workspace_root, image=sandbox_image)
    )
    applier = SafePatchApplier(
        policy,
        runner,
        receipt_path=str(state_root / "receipts.sqlite3"),
    )
    planner = RuleBasedPatchPlanner(policy)
    return RepairWorkflow(
        tools,
        planner,
        applier,
        checkpoint_path=state_root / "checkpoints.sqlite3",
    )


def build_model_workflow(
    workspace_root: str | Path,
    *,
    state_dir: str | Path,
    base_url: str,
    api_key: str,
    model: str,
    java_executable: str = "java",
    javac_executable: str = "javac",
    sandbox_image: str = "eclipse-temurin:17-jdk-jammy",
) -> tuple[RepairWorkflow, OpenAICompatibleModelCaller]:
    """真实模型的基线、试修和正式复测均须使用 Docker 沙箱。"""
    state_root = Path(state_dir)
    state_root.mkdir(parents=True, exist_ok=True)
    policy = WorkspacePolicy(workspace_root)
    runner = DockerJavacSandboxRunner(workspace_root, image=sandbox_image)
    applier = SafePatchApplier(
        policy,
        runner,
        receipt_path=str(state_root / "receipts.sqlite3"),
    )
    caller = OpenAICompatibleModelCaller(
        base_url=base_url,
        api_key=api_key,
        model=model,
    )
    workflow = RepairWorkflow(
        CodeTools(policy),
        AgenticPatchPlanner(
            policy, caller,
            lambda trial_root: DockerJavacSandboxRunner(
                trial_root, image=sandbox_image,
            ),
        ),
        applier,
        checkpoint_path=state_root / "checkpoints.sqlite3",
    )
    return workflow, caller
