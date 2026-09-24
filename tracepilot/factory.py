"""根据工作区配置组装 TracePilot 的运行时依赖。"""

from __future__ import annotations

from pathlib import Path

from tracepilot.planner import AgenticPatchPlanner, RuleBasedPatchPlanner
from tracepilot.providers import OpenAICompatibleModelCaller
from tracepilot.security import WorkspacePolicy
from tracepilot.tools import CodeTools, JavacMainTestRunner, SafePatchApplier
from tracepilot.workflow import RepairWorkflow


def build_offline_workflow(
    workspace_root: str | Path,
    *,
    state_dir: str | Path,
    java_executable: str = "java",
    javac_executable: str = "javac",
) -> RepairWorkflow:
    """创建可离线复现的工作流；规划器不调用外部模型。"""
    state_root = Path(state_dir)
    state_root.mkdir(parents=True, exist_ok=True)
    policy = WorkspacePolicy(workspace_root)
    tools = CodeTools(policy)
    runner = JavacMainTestRunner(
        workspace_root,
        java_executable=java_executable,
        javac_executable=javac_executable,
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
) -> tuple[RepairWorkflow, OpenAICompatibleModelCaller]:
    """创建真实模型规划工作流；调用方负责在结束时关闭两个对象。"""
    state_root = Path(state_dir)
    state_root.mkdir(parents=True, exist_ok=True)
    policy = WorkspacePolicy(workspace_root)
    runner = JavacMainTestRunner(
        workspace_root,
        java_executable=java_executable,
        javac_executable=javac_executable,
    )
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
            lambda trial_root: JavacMainTestRunner(
                trial_root,
                java_executable=java_executable,
                javac_executable=javac_executable,
            ),
        ),
        applier,
        checkpoint_path=state_root / "checkpoints.sqlite3",
    )
    return workflow, caller
