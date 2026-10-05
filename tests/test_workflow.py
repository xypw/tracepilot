from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langgraph.types import Command

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
        trusted_local_runner=True,
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


class SourceResultRunner:
    """No Java process: observe whether the fixture contains the proposed fix."""

    def __init__(self, root: Path) -> None:
        self.source = root / "src/main/java/demo/CustomerService.java"
        self.calls = 0

    def run(self, test_target: str) -> RunnerResult:
        self.calls += 1
        passed = "customerName == null" in self.source.read_text(encoding="utf-8")
        return RunnerResult(
            passed=passed, command=["fake-test", test_target],
            return_code=0 if passed else 1, output_tail="fixture snapshot",
        )


def build_fake(java_fixture: Path, state: Path) -> RepairWorkflow:
    state.mkdir(exist_ok=True)
    policy = WorkspacePolicy(java_fixture)
    return RepairWorkflow(
        CodeTools(policy), RuleBasedPatchPlanner(policy),
        SafePatchApplier(policy, SourceResultRunner(java_fixture),
                         receipt_path=str(state / "receipts.sqlite3")),
        checkpoint_path=state / "checkpoints.sqlite3",
    )


def start_fake(workflow: RepairWorkflow):
    return workflow.start(RepairRequest(
        failure_text=FAILURE, test_target="demo.CustomerServiceTest",
    ))


def test_new_run_executes_same_patch_again_after_user_reverts_source(
    java_fixture: Path, tmp_path: Path,
) -> None:
    workflow = build_fake(java_fixture, tmp_path / "state")
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    original = source.read_bytes()
    try:
        first = start_fake(workflow)
        workflow.resume(first.run_id, ApprovalRequest(
            approved=True, proposal_digest=first.proposal.proposal_digest,
        ))
        source.write_bytes(original)
        second = start_fake(workflow)
        assert not second.baseline_test_result.passed
        assert first.proposal.proposal_digest == second.proposal.proposal_digest
        calls_before = workflow.applier.runner.calls
        result = workflow.resume(second.run_id, ApprovalRequest(
            approved=True, proposal_digest=second.proposal.proposal_digest,
        ))
        assert result.status == RunStatus.COMPLETED
        assert workflow.applier.runner.calls == calls_before + 1
        assert source.read_bytes() != original
    finally:
        workflow.close()


@pytest.mark.parametrize("first_decision", [True, False])
def test_persisted_approval_rejects_conflicting_decision_and_digest(
    java_fixture: Path, tmp_path: Path, first_decision: bool,
) -> None:
    state = tmp_path / "state"
    workflow = build_fake(java_fixture, state)
    waiting = start_fake(workflow)
    approval = ApprovalRequest(
        approved=first_decision, proposal_digest=waiting.proposal.proposal_digest,
    )
    accepted = workflow.resume(waiting.run_id, approval)
    workflow.close()
    restored = build_fake(java_fixture, state)
    try:
        assert restored.resume(waiting.run_id, approval).status == accepted.status
        with pytest.raises(ValueError, match="审批决定"):
            restored.resume(waiting.run_id, approval.model_copy(update={
                "approved": not first_decision,
            }))
        with pytest.raises(ValueError, match="指纹"):
            restored.resume(waiting.run_id, approval.model_copy(update={
                "proposal_digest": "0" * 64,
            }))
        assert restored.get(waiting.run_id).status == accepted.status
        assert restored.applier.runner.calls == 0
    finally:
        restored.close()


@pytest.mark.parametrize("first_decision,second_decision", [
    (True, False), (False, True), (True, True), (False, False),
])
def test_concurrent_approval_resumes_one_run_only_once(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    first_decision: bool, second_decision: bool,
) -> None:
    workflow = build_fake(java_fixture, tmp_path / "state")
    waiting = start_fake(workflow)
    entered = Event()
    release = Event()
    second_entered_graph = Event()
    original_invoke = workflow.graph.invoke

    def paused_invoke(*args, **kwargs):
        if entered.is_set():
            second_entered_graph.set()
        else:
            entered.set()
            assert release.wait(timeout=5)
        return original_invoke(*args, **kwargs)

    monkeypatch.setattr(workflow.graph, "invoke", paused_invoke)

    def submit(decision: bool):
        return workflow.resume(waiting.run_id, ApprovalRequest(
            approved=decision, proposal_digest=waiting.proposal.proposal_digest,
        ))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, first_decision)
            assert entered.wait(timeout=5)
            second = pool.submit(submit, second_decision)
            overlapped = second_entered_graph.wait(timeout=0.2)
            release.set()
            first_result = first.result(timeout=5)
            if first_decision != second_decision:
                with pytest.raises(ValueError, match="审批决定"):
                    second.result(timeout=5)
            else:
                assert second.result(timeout=5).status == first_result.status
        assert not overlapped
        expected = RunStatus.COMPLETED if first_decision else RunStatus.CANCELED
        assert workflow.get(waiting.run_id).status == expected
        assert workflow.applier.runner.calls == (2 if first_decision else 1)
    finally:
        release.set()
        workflow.close()


def test_successful_test_with_external_edit_returns_unknown(
    java_fixture: Path, tmp_path: Path,
) -> None:
    workflow = build_fake(java_fixture, tmp_path / "state")
    waiting = start_fake(workflow)
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    external_edit = source.read_bytes() + b"\n// concurrent save\n"

    class EditingRunner:
        def run(self, target: str) -> RunnerResult:
            source.write_bytes(external_edit)
            return RunnerResult(passed=True, command=["fake", target],
                                return_code=0, output_tail="old snapshot passed")

    workflow.applier.runner = EditingRunner()
    try:
        result = workflow.resume(waiting.run_id, ApprovalRequest(
            approved=True, proposal_digest=waiting.proposal.proposal_digest,
        ))
        assert result.status == RunStatus.RESULT_UNKNOWN
        assert source.read_bytes() == external_edit
        assert result.test_result is None
    finally:
        workflow.close()


@pytest.mark.parametrize("execution_state", ["not_started", "passed", "failed", "unknown"])
def test_persisted_approval_recovers_without_repeating_an_execution(
    java_fixture: Path, tmp_path: Path, execution_state: str,
) -> None:
    state_dir = tmp_path / "state"
    workflow = build_fake(java_fixture, state_dir)
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    original = source.read_bytes()
    waiting = start_fake(workflow)
    approval = ApprovalRequest(
        approved=True, proposal_digest=waiting.proposal.proposal_digest,
    )
    try:
        # Stop after the approval checkpoint, before the apply node can save its
        # result. Simulate the different receipt states left by a process exit.
        workflow.graph.invoke(
            Command(resume=approval.model_dump()),
            config=workflow._config(waiting.run_id), interrupt_after=["approval"],
        )
        snapshot = workflow.graph.get_state(workflow._config(waiting.run_id))
        assert snapshot.values["approved"] is True
        assert snapshot.next == ("apply",)

        if execution_state in {"failed", "unknown"}:
            class InterruptedRunner:
                def run(self, test_target: str) -> RunnerResult:
                    if execution_state == "unknown":
                        raise TimeoutError("execution interrupted")
                    return RunnerResult(
                        passed=False, command=["fake", test_target],
                        return_code=1, output_tail="failed",
                    )

            workflow.applier.runner = InterruptedRunner()
        if execution_state == "unknown":
            with pytest.raises(TimeoutError):
                workflow.applier.apply_and_test(
                    waiting.proposal, execution_id=waiting.run_id,
                )
        elif execution_state != "not_started":
            workflow.applier.apply_and_test(
                waiting.proposal, execution_id=waiting.run_id,
            )
    finally:
        workflow.close()

    restored = build_fake(java_fixture, state_dir)
    try:
        with pytest.raises(ValueError, match="审批决定"):
            restored.resume(waiting.run_id, approval.model_copy(update={"approved": False}))
        assert restored.get(waiting.run_id).status == RunStatus.DIAGNOSING
        result = restored.resume(waiting.run_id, approval)
        expected = {
            "not_started": RunStatus.COMPLETED,
            "passed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "unknown": RunStatus.RESULT_UNKNOWN,
        }[execution_state]
        assert result.status == expected
        assert restored.applier.runner.calls == (1 if execution_state == "not_started" else 0)
        assert (source.read_bytes() == original) == (execution_state in {"failed", "unknown"})
        assert restored.resume(waiting.run_id, approval) == result
        assert restored.applier.runner.calls == (1 if execution_state == "not_started" else 0)
    finally:
        restored.close()


@pytest.mark.parametrize("decision", [None, True, False])
def test_existing_run_id_cannot_reset_persisted_workflow(
    java_fixture: Path, tmp_path: Path, decision: bool | None,
) -> None:
    state_dir = tmp_path / "state"
    workflow = build_fake(java_fixture, state_dir)
    request = RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest")
    try:
        waiting = workflow.start(request, run_id="fixed-run-id")
        previous = waiting if decision is None else workflow.resume(
            waiting.run_id, ApprovalRequest(
                approved=decision, proposal_digest=waiting.proposal.proposal_digest,
            ),
        )
    finally:
        workflow.close()

    restored = build_fake(java_fixture, state_dir)
    source = java_fixture / "src/main/java/demo/CustomerService.java"
    before = source.read_bytes()
    try:
        with pytest.raises(ValueError, match="run_id已存在"):
            restored.start(request, run_id=waiting.run_id)
        assert restored.get(waiting.run_id) == previous
        assert restored.applier.runner.calls == 0
        assert source.read_bytes() == before
    finally:
        restored.close()


def test_concurrent_start_cannot_reuse_an_execution_identity(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = build_fake(java_fixture, tmp_path / "state")
    entered = Event()
    release = Event()
    original_invoke = workflow.graph.invoke

    def paused_invoke(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original_invoke(*args, **kwargs)

    monkeypatch.setattr(workflow.graph, "invoke", paused_invoke)
    request = RepairRequest(failure_text=FAILURE, test_target="demo.CustomerServiceTest")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(workflow.start, request, run_id="same-run")
            assert entered.wait(timeout=5)
            second = pool.submit(workflow.start, request, run_id="same-run")
            release.set()
            waiting = first.result(timeout=5)
            with pytest.raises(ValueError, match="run_id已存在"):
                second.result(timeout=5)
        assert workflow.get("same-run") == waiting
        assert workflow.applier.runner.calls == 1
    finally:
        release.set()
        workflow.close()
