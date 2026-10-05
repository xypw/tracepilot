from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError
import pytest

from tracepilot.models import PatchProposal, TestResult as RunnerResult
from tracepilot.planner import RuleBasedPatchPlanner
from tracepilot.security import WorkspacePolicy, WorkspaceViolation
from tracepilot.tools import CodeTools, SafePatchApplier


class PassingRunner:
    def run(self, test_target: str) -> RunnerResult:
        return RunnerResult(
            passed=True,
            command=["test", test_target],
            return_code=0,
            output_tail="ok",
        )


class FailingRunner:
    def run(self, test_target: str) -> RunnerResult:
        return RunnerResult(
            passed=False,
            command=["test", test_target],
            return_code=1,
            output_tail="failed",
        )


def proposal_for(java_fixture: Path) -> PatchProposal:
    policy = WorkspacePolicy(java_fixture)
    candidates = CodeTools(policy).locate_stack_files("CustomerService.java:5")
    return RuleBasedPatchPlanner(policy).propose(
        failure_text="NPE",
        candidate_files=candidates,
        test_target="demo.CustomerServiceTest",
    )


def test_test_failure_rolls_back_source(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    before = policy.read_text(proposal.relative_path)
    applier = SafePatchApplier(policy, FailingRunner())
    result, rolled_back = applier.apply_and_test(proposal)
    assert not result.passed
    assert rolled_back
    assert policy.read_text(proposal.relative_path) == before
    applier.close()


def test_repeated_apply_returns_receipt_without_second_write(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    applier = SafePatchApplier(policy, PassingRunner())
    first, _ = applier.apply_and_test(proposal)
    after = policy.read_text(proposal.relative_path)
    second, _ = applier.apply_and_test(proposal)
    assert first == second
    assert policy.read_text(proposal.relative_path) == after
    applier.close()


def test_changed_source_invalidates_confirmed_patch(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    path = policy.resolve_file(proposal.relative_path, writable=True)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    applier = SafePatchApplier(policy, PassingRunner())
    with pytest.raises(WorkspaceViolation, match="源文件已变化"):
        applier.apply_and_test(proposal)
    applier.close()


def test_tampered_patch_digest_is_rejected(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    tampered = proposal.model_copy(update={"new_text": "return \"hacked\";"})
    applier = SafePatchApplier(policy, PassingRunner())
    with pytest.raises(WorkspaceViolation, match="摘要校验失败"):
        applier.apply_and_test(tampered)
    applier.close()


def test_preview_rejects_test_source_before_approval(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    relative = "src/test/java/demo/CustomerServiceTest.java"
    before = policy.read_text(relative)
    proposal = PatchProposal.create(
        relative_path=relative, old_text=before, new_text="// remove test",
        explanation="test-only proposal must be rejected", test_target="demo.CustomerServiceTest",
        source_sha256=policy.source_sha256(relative),
    )
    applier = SafePatchApplier(policy, PassingRunner())
    try:
        with pytest.raises(WorkspaceViolation, match="生产源码"):
            applier.preview(proposal)
        assert policy.read_text(relative) == before
    finally:
        applier.close()


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("suffix", ["", " // 中文"])
def test_oversized_patch_is_rejected_before_preview_or_execution(
    java_fixture: Path, line_ending: bytes, suffix: str,
) -> None:
    path = java_fixture / "src/main/java/demo/CustomerService.java"
    before = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending)
    path.write_bytes(before)
    initial = proposal_for(java_fixture)
    proposal = PatchProposal.create(**{
        **initial.model_dump(exclude={"proposal_id", "proposal_digest"}),
        "new_text": initial.new_text + suffix,
    })
    updated = before.decode("utf-8").replace(proposal.old_text, proposal.new_text, 1).encode("utf-8")
    policy = WorkspacePolicy(java_fixture, max_file_bytes=len(updated) - 1)
    runner = Mock(spec=PassingRunner)
    applier = SafePatchApplier(policy, runner)
    try:
        with pytest.raises(WorkspaceViolation, match="补丁后文件超过大小限制"):
            applier.preview(proposal)
        with pytest.raises(WorkspaceViolation, match="补丁后文件超过大小限制"):
            applier.apply_and_test(proposal, execution_id="oversized-run")
        assert path.read_bytes() == before
        runner.run.assert_not_called()
        assert applier.receipt_status(proposal, execution_id="oversized-run") is None
    finally:
        applier.close()


def test_patch_at_file_size_limit_is_allowed(java_fixture: Path) -> None:
    proposal = proposal_for(java_fixture)
    path = java_fixture / proposal.relative_path
    updated = path.read_bytes().decode("utf-8").replace(
        proposal.old_text, proposal.new_text, 1,
    ).encode("utf-8")
    policy = WorkspacePolicy(java_fixture, max_file_bytes=len(updated))
    applier = SafePatchApplier(policy, PassingRunner())
    try:
        assert applier.preview(proposal)
        result, rolled_back = applier.apply_and_test(proposal, execution_id="limit-run")
        assert result.passed and not rolled_back
        assert path.read_bytes() == updated
        assert applier.receipt_status(proposal, execution_id="limit-run") == "DONE"
    finally:
        applier.close()


@pytest.mark.parametrize("passed", [True, False])
def test_external_edit_during_test_is_preserved_and_never_gets_done_receipt(
    java_fixture: Path, passed: bool,
) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    path = policy.resolve_file(proposal.relative_path, writable=True)
    external_edit = path.read_bytes() + b"\n// saved by another editor\n"

    class EditingRunner:
        def run(self, test_target: str) -> RunnerResult:
            path.write_bytes(external_edit)
            return RunnerResult(
                passed=passed, command=["test", test_target],
                return_code=0 if passed else 1, output_tail="snapshot result",
            )

    applier = SafePatchApplier(policy, EditingRunner())
    try:
        with pytest.raises(WorkspaceViolation, match="RESULT_UNKNOWN"):
            applier.apply_and_test(proposal)
        assert path.read_bytes() == external_edit
        assert applier.receipt_status(proposal) == "EXECUTING"
    finally:
        applier.close()


def test_execution_receipt_survives_reopen_without_reapplying(
    java_fixture: Path, tmp_path: Path,
) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    receipt_path = str(tmp_path / "receipts.sqlite3")
    applier = SafePatchApplier(policy, PassingRunner(), receipt_path=receipt_path)
    try:
        first, _ = applier.apply_and_test(proposal, execution_id="run-one")
    finally:
        applier.close()

    class UnexpectedRunner:
        def run(self, test_target: str) -> RunnerResult:
            raise AssertionError("the same execution must not run twice")

    restored = SafePatchApplier(policy, UnexpectedRunner(), receipt_path=receipt_path)
    try:
        repeated, _ = restored.apply_and_test(proposal, execution_id="run-one")
        assert repeated == first
        assert restored.receipt_status(proposal, execution_id="run-one") == "DONE"
    finally:
        restored.close()


@pytest.mark.parametrize("legacy_status", ["EXECUTING", "DONE"])
def test_legacy_receipt_cannot_be_silently_reused_as_new_execution(
    java_fixture: Path, tmp_path: Path, legacy_status: str,
) -> None:
    policy = WorkspacePolicy(java_fixture)
    proposal = proposal_for(java_fixture)
    original = policy.read_text(proposal.relative_path)
    applier = SafePatchApplier(
        policy, PassingRunner(), receipt_path=str(tmp_path / "legacy.sqlite3"),
    )
    historical_result = PassingRunner().run(proposal.test_target).model_dump_json()
    applier.receipts.execute(
        "INSERT INTO receipts VALUES (?, ?, ?)",
        (str(policy.root) + ":" + proposal.proposal_digest, legacy_status,
         historical_result if legacy_status == "DONE" else None),
    )
    applier.receipts.commit()
    try:
        if legacy_status == "EXECUTING":
            with pytest.raises(WorkspaceViolation, match="RESULT_UNKNOWN"):
                applier.apply_and_test(proposal, execution_id="new-run")
            assert policy.read_text(proposal.relative_path) == original
        else:
            result, _ = applier.apply_and_test(proposal, execution_id="new-run")
            assert result.passed
            assert policy.read_text(proposal.relative_path) != original
    finally:
        applier.close()
