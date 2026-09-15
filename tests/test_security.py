from __future__ import annotations

from pathlib import Path

import pytest

from tracepilot.security import WorkspacePolicy, WorkspaceViolation


def test_policy_rejects_path_traversal(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.read_text("../secret.java")


def test_policy_only_allows_writes_to_production_java(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.resolve_file(
            "src/test/java/demo/CustomerServiceTest.java", writable=True
        )


def test_policy_rejects_non_allowlisted_suffix(java_fixture: Path) -> None:
    file_path = java_fixture / "secret.txt"
    file_path.write_text("secret", encoding="utf-8")
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.read_text("secret.txt")
