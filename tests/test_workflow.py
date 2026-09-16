from __future__ import annotations

from pathlib import Path

from tracepilot.factory import build_offline_workflow
from tracepilot.models import ApprovalRequest, RepairRequest, RunStatus, TestResult as RunnerResult
from tracepilot.planner import RuleBasedPatchPlanner
from tracepilot.security import WorkspacePolicy
from tracepilot.tools import CodeTools, SafePatchApplier
from tracepilot.workflow import RepairWorkflow


FAILURE = (
    "java.lang.NullPointerException\n"
    "at demo.CustomerService.displayName(CustomerService.java:5)"
)


def build(java_fixture: Path, state: Path):
    return build_offline_workflow(
        java_fixture,
        state_dir=state,
        java_executable="java",
        javac_executable="javac",
    )


def test_workflow_waits_recovers_and_executes_after_confirmation(
    java_fixture: Path, tmp_path: Path
) -> None:
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    before = source.read_text(encoding="utf-8")
    workflow = build(java_fixture, tmp_path / "state")
    waiting = workflow.start(
        RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest"),
        run_id="run-recovery",
    )
    assert waiting.status == RunStatus.WAITING_APPROVAL
    assert waiting.baseline_test_result and not waiting.baseline_test_result.passed
    assert source.read_text(encoding="utf-8") == before
    digest = waiting.proposal.proposal_digest
    workflow.close()

    restored = build(java_fixture, tmp_path / "state")
    assert restored.get("run-recovery").status == RunStatus.WAITING_APPROVAL
    completed = restored.resume(
        "run-recovery",
        ApprovalRequest(approved=True, proposal_digest=digest),
    )
    assert completed.status == RunStatus.COMPLETED
    assert completed.test_result and completed.test_result.passed
    assert source.read_text(encoding="utf-8") != before
    repeated = restored.resume(
        "run-recovery",
        ApprovalRequest(approved=True, proposal_digest=digest),
    )
    assert repeated.status == RunStatus.COMPLETED
    restored.close()


def test_workflow_stops_when_failure_cannot_be_reproduced(
    java_fixture: Path, tmp_path: Path
) -> None:
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "return customerName.trim();",
            'return customerName == null ? "anonymous" : customerName.trim();',
        ),
        encoding="utf-8",
    )
    workflow = build(java_fixture, tmp_path / "state-passing")
    result = workflow.start(
        RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest")
    )
    assert result.status == RunStatus.NOT_REPRODUCED
    assert result.proposal is None
    assert result.error == "FAILURE_NOT_REPRODUCED"
    workflow.close()


def test_rejected_patch_does_not_change_file(java_fixture: Path, tmp_path: Path) -> None:
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    before = source.read_text(encoding="utf-8")
    workflow = build(java_fixture, tmp_path / "state")
    waiting = workflow.start(
        RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest")
    )
    canceled = workflow.resume(
        waiting.run_id,
        ApprovalRequest(
            approved=False,
            proposal_digest=waiting.proposal.proposal_digest,
        ),
    )
    assert canceled.status == RunStatus.CANCELED
    assert source.read_text(encoding="utf-8") == before
    workflow.close()


def test_execution_exception_returns_unknown_and_restores_source(
    java_fixture: Path, tmp_path: Path
) -> None:
    class ExplodingRunner:
        calls = 0

        def run(self, test_target: str) -> RunnerResult:
            self.calls += 1
            if self.calls == 1:
                return RunnerResult(
                    passed=False,
                    command=["test", test_target],
                    return_code=1,
                    output_tail="reproduced",
                )
            raise TimeoutError("test timeout")

    source = java_fixture / "src/main/java/demo/CustomerService.java"
    before = source.read_text(encoding="utf-8")
    policy = WorkspacePolicy(java_fixture)
    applier = SafePatchApplier(
        policy,
        ExplodingRunner(),
        receipt_path=str(tmp_path / "unknown-receipts.sqlite3"),
    )
    workflow = RepairWorkflow(
        CodeTools(policy),
        RuleBasedPatchPlanner(policy),
        applier,
        checkpoint_path=tmp_path / "unknown-checkpoints.sqlite3",
    )
    waiting = workflow.start(
        RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest")
    )
    result = workflow.resume(
        waiting.run_id,
        ApprovalRequest(
            approved=True,
            proposal_digest=waiting.proposal.proposal_digest,
        ),
    )
    assert result.status == RunStatus.RESULT_UNKNOWN
    assert result.error and "TimeoutError" in result.error
    assert source.read_text(encoding="utf-8") == before
    workflow.close()
