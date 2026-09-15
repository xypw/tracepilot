from __future__ import annotations

from pathlib import Path

from tracepilot.factory import build_offline_workflow
from tracepilot.models import ApprovalRequest, RepairRequest, RunStatus


FAILURE = (
    "java.lang.NullPointerException\n"
    "at demo.CustomerService.displayName(CustomerService.java:5)"
)


def build(java_fixture: Path, state: Path):
    return build_offline_workflow(
        java_fixture,
        state_dir=state,
        java_executable=r"D:\jdk\bin\java.exe",
        javac_executable=r"D:\jdk\bin\javac.exe",
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
