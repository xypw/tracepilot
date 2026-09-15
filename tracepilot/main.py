"""TracePilot 本地演示 API 入口。"""

from __future__ import annotations

import os
from pathlib import Path

from tracepilot.api import create_app
from tracepilot.factory import build_model_workflow, build_offline_workflow


workspace_root = os.environ.get("TRACEPILOT_WORKSPACE_ROOT")
if not workspace_root:
    raise RuntimeError("请设置 TRACEPILOT_WORKSPACE_ROOT 为待诊断 Java 示例仓库")

state_dir = os.environ.get(
    "TRACEPILOT_STATE_DIR",
    str(Path(workspace_root) / ".tracepilot"),
)
planner_mode = os.environ.get("TRACEPILOT_PLANNER", "offline")
if planner_mode == "model":
    required = {
        "TRACEPILOT_MODEL_BASE_URL": os.environ.get("TRACEPILOT_MODEL_BASE_URL"),
        "TRACEPILOT_MODEL_API_KEY": os.environ.get("TRACEPILOT_MODEL_API_KEY"),
        "TRACEPILOT_MODEL": os.environ.get("TRACEPILOT_MODEL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"真实模型模式缺少环境变量: {', '.join(missing)}")
    workflow, model_caller = build_model_workflow(
        workspace_root,
        state_dir=state_dir,
        base_url=required["TRACEPILOT_MODEL_BASE_URL"],
        api_key=required["TRACEPILOT_MODEL_API_KEY"],
        model=required["TRACEPILOT_MODEL"],
        java_executable=os.environ.get("TRACEPILOT_JAVA", "java"),
        javac_executable=os.environ.get("TRACEPILOT_JAVAC", "javac"),
    )
elif planner_mode == "offline":
    workflow = build_offline_workflow(
        workspace_root,
        state_dir=state_dir,
        java_executable=os.environ.get("TRACEPILOT_JAVA", "java"),
        javac_executable=os.environ.get("TRACEPILOT_JAVAC", "javac"),
    )
else:
    raise RuntimeError("TRACEPILOT_PLANNER 只允许 offline 或 model")
app = create_app(workflow)
