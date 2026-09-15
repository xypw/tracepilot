"""LangGraph诊断、审批和受控执行工作流。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from tracepilot.models import (
    ApprovalRequest,
    PatchProposal,
    RepairRequest,
    RepairRunResponse,
    RunStatus,
    TestResult,
    ToolTrace,
)
from tracepilot.planner import PatchPlanner
from tracepilot.tools import CodeTools, SafePatchApplier


class RepairState(TypedDict, total=False):
    run_id: str
    failure_text: str
    test_target: str
    status: str
    candidate_files: list[str]
    proposal: dict[str, Any]
    diff_preview: str
    approved: bool
    baseline_test_result: dict[str, Any]
    test_result: dict[str, Any]
    trace: list[dict[str, Any]]
    error: str


class RepairWorkflow:
    def __init__(
        self,
        code_tools: CodeTools,
        planner: PatchPlanner,
        applier: SafePatchApplier,
        *,
        checkpoint_path: str | Path = ":memory:",
    ) -> None:
        self.code_tools = code_tools
        self.planner = planner
        self.applier = applier
        if checkpoint_path != ":memory:":
            checkpoint = Path(checkpoint_path)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path = checkpoint
        self._checkpoint_connection = sqlite3.connect(
            str(checkpoint_path), check_same_thread=False
        )
        self._checkpointer = SqliteSaver(self._checkpoint_connection)
        self.graph = self._build_graph()

    def close(self) -> None:
        self._checkpoint_connection.close()
        self.applier.close()

    @staticmethod
    def _event(state: RepairState, tool: str, summary: str) -> list[dict[str, Any]]:
        trace = list(state.get("trace", []))
        trace.append(ToolTrace(step=len(trace) + 1, tool=tool, summary=summary).model_dump())
        return trace

    def _locate(self, state: RepairState) -> dict[str, Any]:
        candidates = self.code_tools.locate_stack_files(state["failure_text"])
        if not candidates:
            raise ValueError("Stack Trace中的Java文件未在仓库内找到")
        return {
            "candidate_files": candidates,
            "trace": self._event(state, "locate_stack_files", f"定位到{len(candidates)}个候选文件"),
        }

    def _propose(self, state: RepairState) -> dict[str, Any]:
        proposal = self.planner.propose(
            failure_text=state["failure_text"],
            candidate_files=state["candidate_files"],
            test_target=state["test_target"],
        )
        return {
            "proposal": proposal.model_dump(),
            "diff_preview": self.applier.preview(proposal),
            "status": RunStatus.WAITING_APPROVAL.value,
            "trace": self._event(state, "propose_patch", proposal.explanation),
        }

    def _verify_failure(self, state: RepairState) -> dict[str, Any]:
        result = self.applier.runner.run(state["test_target"])
        reproduced = not result.passed
        return {
            "baseline_test_result": result.model_dump(),
            "status": (
                RunStatus.DIAGNOSING.value
                if reproduced
                else RunStatus.NOT_REPRODUCED.value
            ),
            "error": None if reproduced else "FAILURE_NOT_REPRODUCED",
            "trace": self._event(
                state,
                "run_targeted_test",
                "已复现测试失败" if reproduced else "测试已经通过，停止自动修复",
            ),
        }

    @staticmethod
    def _after_verify(state: RepairState) -> str:
        return "propose" if state["status"] == RunStatus.DIAGNOSING.value else "done"

    def _approval(self, state: RepairState) -> dict[str, Any]:
        proposal = PatchProposal.model_validate(state["proposal"])
        raw_decision = interrupt({
            "run_id": state["run_id"],
            "proposal_id": proposal.proposal_id,
            "proposal_digest": proposal.proposal_digest,
            "diff_preview": state["diff_preview"],
        })
        decision = ApprovalRequest.model_validate(raw_decision)
        if decision.proposal_digest != proposal.proposal_digest:
            raise ValueError("确认绑定的补丁指纹不匹配")
        return {
            "approved": decision.approved,
            "status": RunStatus.DIAGNOSING.value if decision.approved else RunStatus.CANCELED.value,
            "trace": self._event(
                state,
                "human_approval",
                "用户批准补丁" if decision.approved else "用户拒绝补丁",
            ),
        }

    @staticmethod
    def _after_approval(state: RepairState) -> str:
        return "apply" if state.get("approved") else "canceled"

    def _apply(self, state: RepairState) -> dict[str, Any]:
        proposal = PatchProposal.model_validate(state["proposal"])
        try:
            result, rolled_back = self.applier.apply_and_test(proposal)
        except Exception as error:
            # 写入开始后异常时不能猜测结果，停止自动重试并交给人工核对。
            return {
                "status": RunStatus.RESULT_UNKNOWN.value,
                "error": f"RESULT_UNKNOWN: {type(error).__name__}: {error}",
                "trace": self._event(
                    state,
                    "apply_patch_and_test",
                    "执行未形成完成回执，需要人工检查工作区",
                ),
            }
        status = RunStatus.COMPLETED if result.passed else RunStatus.FAILED
        summary = "补丁通过定向测试" if result.passed else (
            "测试失败，已自动回滚" if rolled_back else "测试失败"
        )
        return {
            "test_result": result.model_dump(),
            "status": status.value,
            "error": None if result.passed else "PATCH_TEST_FAILED_AND_ROLLED_BACK",
            "trace": self._event(state, "apply_patch_and_test", summary),
        }

    def _canceled(self, state: RepairState) -> dict[str, Any]:
        return {"trace": self._event(state, "cancel_patch", "未修改任何文件")}

    def _build_graph(self):
        builder = StateGraph(RepairState)
        builder.add_node("locate", self._locate)
        builder.add_node("verify_failure", self._verify_failure)
        builder.add_node("propose", self._propose)
        builder.add_node("approval", self._approval)
        builder.add_node("apply", self._apply)
        builder.add_node("canceled", self._canceled)
        builder.add_edge(START, "locate")
        builder.add_edge("locate", "verify_failure")
        builder.add_conditional_edges(
            "verify_failure",
            self._after_verify,
            {"propose": "propose", "done": END},
        )
        builder.add_edge("propose", "approval")
        builder.add_conditional_edges(
            "approval", self._after_approval, {"apply": "apply", "canceled": "canceled"}
        )
        builder.add_edge("apply", END)
        builder.add_edge("canceled", END)
        return builder.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def start(self, request: RepairRequest, *, run_id: str | None = None) -> RepairRunResponse:
        run_id = run_id or f"run-{uuid4().hex}"
        self.graph.invoke({
            "run_id": run_id,
            "failure_text": request.failure_text,
            "test_target": request.test_target,
            "status": RunStatus.DIAGNOSING.value,
            "candidate_files": [],
            "trace": [],
        }, config=self._config(run_id))
        return self.get(run_id)

    def resume(self, run_id: str, approval: ApprovalRequest) -> RepairRunResponse:
        current = self.get(run_id)
        if current.status in {
            RunStatus.COMPLETED,
            RunStatus.CANCELED,
            RunStatus.FAILED,
            RunStatus.RESULT_UNKNOWN,
            RunStatus.NOT_REPRODUCED,
        }:
            return current
        if current.status != RunStatus.WAITING_APPROVAL or current.proposal is None:
            raise ValueError("当前任务不在等待确认状态")
        if approval.proposal_digest != current.proposal.proposal_digest:
            raise ValueError("确认绑定的补丁指纹不匹配")
        self.graph.invoke(Command(resume=approval.model_dump()), config=self._config(run_id))
        return self.get(run_id)

    def get(self, run_id: str) -> RepairRunResponse:
        snapshot = self.graph.get_state(self._config(run_id))
        if not snapshot.values:
            raise KeyError(run_id)
        state = snapshot.values
        return RepairRunResponse(
            run_id=run_id,
            status=RunStatus(state["status"]),
            candidate_files=state.get("candidate_files", []),
            proposal=PatchProposal.model_validate(state["proposal"]) if state.get("proposal") else None,
            diff_preview=state.get("diff_preview"),
            baseline_test_result=(
                TestResult.model_validate(state["baseline_test_result"])
                if state.get("baseline_test_result")
                else None
            ),
            test_result=TestResult.model_validate(state["test_result"]) if state.get("test_result") else None,
            trace=[ToolTrace.model_validate(item) for item in state.get("trace", [])],
            error=state.get("error"),
        )
