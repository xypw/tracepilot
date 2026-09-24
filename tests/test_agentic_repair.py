"""模型动作由固定回放替代，验证调查反馈和正式写入边界。"""

from __future__ import annotations

from pathlib import Path
import shutil

import httpx
import pytest

from tracepilot.models import ApprovalRequest, RepairRequest, RunStatus, TestResult as RunnerResult
from tracepilot.planner import AgenticPatchPlanner, RuleBasedPatchPlanner
from tracepilot.security import WorkspacePolicy, WorkspaceViolation
from tracepilot.tools import CodeTools, JavacMainTestRunner, SafePatchApplier
from tracepilot.workflow import RepairWorkflow


SOURCE = "src/main/java/demo/CustomerService.java"
OLD = "return customerName.trim();"
GOOD = 'return customerName == null ? "anonymous" : customerName.trim();'


class ContentRunner:
    def __init__(self, root: Path):
        self.root = root

    def run(self, test_target: str) -> RunnerResult:
        content = (self.root / SOURCE).read_text(encoding="utf-8")
        passed = GOOD in content
        return RunnerResult(
            passed=passed, command=["fake-targeted-test", test_target],
            return_code=0 if passed else 1,
            output_tail="passed" if passed else "expected anonymous for null name",
        )


class ReplayModel:
    def __init__(self, actions: list[dict]):
        self.actions = list(actions)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        if not self.actions:
            raise AssertionError("模型被额外调用")
        return self.actions.pop(0)


def patch_action(new_text: str) -> dict:
    return {"action": "patch", "relative_path": SOURCE, "old_text": OLD,
            "new_text": new_text, "explanation": "处理空客户名"}


def build(java_fixture: Path, state: Path, model: ReplayModel) -> RepairWorkflow:
    state.mkdir(parents=True, exist_ok=True)
    policy = WorkspacePolicy(java_fixture)
    return RepairWorkflow(
        CodeTools(policy),
        AgenticPatchPlanner(policy, model, lambda root: ContentRunner(root)),
        SafePatchApplier(policy, ContentRunner(java_fixture),
                         receipt_path=str(state / "receipts.sqlite3")),
        checkpoint_path=state / "checkpoints.sqlite3",
    )


def test_failed_trial_feedback_leads_to_second_patch_without_real_write(
    java_fixture: Path, tmp_path: Path
) -> None:
    source = java_fixture / SOURCE
    before = source.read_bytes()
    model = ReplayModel([
        {"action": "search", "query": "displayName"},
        {"action": "read", "relative_path": SOURCE},
        patch_action("return customerName;"),
        patch_action(GOOD),
    ])
    workflow = build(java_fixture, tmp_path / "state", model)
    try:
        waiting = workflow.start(RepairRequest(
            failure_text="at demo.CustomerService.displayName(CustomerService.java:5)",
            test_target="demo.CustomerServiceTest",
        ))
        assert waiting.status == RunStatus.WAITING_APPROVAL
        assert source.read_bytes() == before
        assert [item.tool for item in waiting.trace].count("trial_patch") == 2
        assert any(item.tool == "search_code" for item in waiting.trace)
        assert "expected anonymous" in model.prompts[-1]
        completed = workflow.resume(waiting.run_id, ApprovalRequest(
            approved=True, proposal_digest=waiting.proposal.proposal_digest,
        ))
        assert completed.status == RunStatus.COMPLETED
        assert GOOD in source.read_text(encoding="utf-8")
    finally:
        workflow.close()


def test_no_stack_frame_can_start_from_code_search(
    java_fixture: Path, tmp_path: Path
) -> None:
    model = ReplayModel([
        {"action": "search", "query": "customerName.trim"},
        {"action": "read", "relative_path": SOURCE},
        patch_action(GOOD),
    ])
    workflow = build(java_fixture, tmp_path / "state", model)
    try:
        waiting = workflow.start(RepairRequest(
            failure_text="assertion failed: expected anonymous for null name",
            test_target="demo.CustomerServiceTest",
        ))
        assert waiting.candidate_files == []
        assert waiting.status == RunStatus.WAITING_APPROVAL
    finally:
        workflow.close()


def test_attempt_limit_fails_closed_and_keeps_workspace_unchanged(
    java_fixture: Path, tmp_path: Path
) -> None:
    source = java_fixture / SOURCE
    before = source.read_bytes()
    model = ReplayModel([
        patch_action("return customerName;"),
        patch_action('return "guest";'),
        patch_action('return "unknown";'),
    ])
    workflow = build(java_fixture, tmp_path / "state", model)
    try:
        failed = workflow.start(RepairRequest(
            failure_text="at demo.CustomerService.displayName(CustomerService.java:5)",
            test_target="demo.CustomerServiceTest",
        ))
        assert failed.status == RunStatus.FAILED
        assert failed.proposal is None
        assert source.read_bytes() == before
        assert len(model.prompts) == 3
    finally:
        workflow.close()


def test_preview_rejects_source_changed_after_proposal(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = RuleBasedPatchPlanner(policy).propose(
        failure_text="NullPointerException", candidate_files=[SOURCE],
        test_target="demo.CustomerServiceTest",
    )
    source = java_fixture / SOURCE
    source.write_text(source.read_text(encoding="utf-8") + "\n// changed", encoding="utf-8")
    applier = SafePatchApplier(policy, ContentRunner(java_fixture))
    try:
        with pytest.raises(WorkspaceViolation, match="源码已变化"):
            applier.preview(proposal)
    finally:
        applier.close()


def test_agentic_repair_runs_real_java_test_before_and_after_approval(
    java_fixture: Path, tmp_path: Path
) -> None:
    source = java_fixture / SOURCE
    before = source.read_bytes()
    model = ReplayModel([
        {"action": "search", "query": "customerName.trim"},
        {"action": "read", "relative_path": SOURCE},
        patch_action(GOOD),
    ])
    policy = WorkspacePolicy(java_fixture)
    java = shutil.which("java")
    javac = shutil.which("javac")
    if not java or not javac:
        pytest.skip("本机未安装测试所需 JDK")
    runner = lambda root: JavacMainTestRunner(
        root, java_executable=java, javac_executable=javac
    )
    state = tmp_path / "state"
    state.mkdir()
    workflow = RepairWorkflow(
        CodeTools(policy), AgenticPatchPlanner(policy, model, runner),
        SafePatchApplier(policy, runner(java_fixture),
                         receipt_path=str(state / "receipts.sqlite3")),
        checkpoint_path=state / "checkpoints.sqlite3",
    )
    try:
        waiting = workflow.start(RepairRequest(
            failure_text="at demo.CustomerService.displayName(CustomerService.java:5)",
            test_target="demo.CustomerServiceTest",
        ))
        assert waiting.status == RunStatus.WAITING_APPROVAL
        assert waiting.baseline_test_result is not None
        assert not waiting.baseline_test_result.passed
        assert source.read_bytes() == before
        assert any(item.tool == "trial_patch" for item in waiting.trace)
        completed = workflow.resume(waiting.run_id, ApprovalRequest(
            approved=True, proposal_digest=waiting.proposal.proposal_digest,
        ))
        assert completed.status == RunStatus.COMPLETED
        assert completed.test_result is not None and completed.test_result.passed
        assert GOOD in source.read_text(encoding="utf-8")
    finally:
        workflow.close()


def test_model_http_failure_is_reported_without_proposal(
    java_fixture: Path, tmp_path: Path
) -> None:
    def failing_model(_prompt: str) -> dict:
        request = httpx.Request("POST", "https://model.invalid/chat/completions")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("provider rate limit", request=request, response=response)

    workflow = build(java_fixture, tmp_path / "state", ReplayModel([]))
    workflow.planner.call_model = failing_model
    before = (java_fixture / SOURCE).read_bytes()
    try:
        failed = workflow.start(RepairRequest(
            failure_text="at demo.CustomerService.displayName(CustomerService.java:5)",
            test_target="demo.CustomerServiceTest",
        ))
        assert failed.status == RunStatus.FAILED
        assert failed.error == "MODEL_UNAVAILABLE: HTTP 429"
        assert failed.proposal is None
        assert (java_fixture / SOURCE).read_bytes() == before
    finally:
        workflow.close()
