from __future__ import annotations

from pathlib import Path

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
