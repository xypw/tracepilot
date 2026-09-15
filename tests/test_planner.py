from __future__ import annotations

from pathlib import Path

from tracepilot.planner import RuleBasedPatchPlanner
from tracepilot.security import WorkspacePolicy
from tracepilot.tools import CodeTools


def test_rule_planner_proposes_source_bound_patch(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    candidates = CodeTools(policy).locate_stack_files(
        "at demo.CustomerService.displayName(CustomerService.java:5)"
    )
    proposal = RuleBasedPatchPlanner(policy).propose(
        failure_text="NullPointerException",
        candidate_files=candidates,
        test_target="demo.CustomerServiceTest",
    )
    assert proposal.relative_path == "src/main/java/demo/CustomerService.java"
    assert proposal.source_sha256 == policy.source_sha256(proposal.relative_path)
    assert len(proposal.proposal_digest) == 64
