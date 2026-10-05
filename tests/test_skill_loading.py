"""固定安装路径的 Skill 必须进入每轮模型请求，且损坏时失败关闭。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from tracepilot import skill_loader
from tracepilot.models import TestResult as RunnerResult
from tracepilot.planner import AgenticPatchPlanner
from tracepilot.security import WorkspacePolicy
from tracepilot.skill_loader import SkillConfigurationError, load_java_test_triage_skill


SOURCE = "src/main/java/demo/CustomerService.java"
SKILL_RELATIVE = Path("skills/java-test-triage/SKILL.md")
VALID_HEADER = "---\nname: java-test-triage\ndescription: 测试规程\n---\n"


class PassingRunner:
    def run(self, test_target: str) -> RunnerResult:
        return RunnerResult(
            passed=True, command=["fake-targeted-test", test_target],
            return_code=0, output_tail="passed",
        )


def patch_action() -> dict:
    return {
        "action": "patch", "relative_path": SOURCE,
        "old_text": "return customerName.trim();",
        "new_text": 'return customerName == null ? "anonymous" : customerName.trim();',
        "explanation": "空客户名直接调用 trim 导致异常，增加默认值。",
    }


def propose(planner: AgenticPatchPlanner):
    return planner.propose(
        failure_text="NullPointerException", candidate_files=[SOURCE],
        test_target="demo.CustomerServiceTest",
    )


def test_packaged_skill_body_reaches_every_model_round(java_fixture: Path) -> None:
    prompts: list[str] = []
    actions = iter([
        {"action": "search", "query": "customerName"},
        {"action": "read", "relative_path": SOURCE},
        patch_action(),
    ])

    def model(prompt: str) -> dict:
        prompts.append(prompt)
        return next(actions)

    outcome = propose(AgenticPatchPlanner(
        WorkspacePolicy(java_fixture), model, lambda _root: PassingRunner(),
    ))

    assert outcome.proposal.relative_path == SOURCE
    assert len(prompts) == 3
    body = load_java_test_triage_skill()
    assert "证据不足时停止，不猜测修复" in body
    for prompt in prompts:
        trusted, observations = prompt.split("\nOBSERVATIONS: ", 1)
        assert f"TRUSTED_SKILL (java-test-triage):\n{body}\nEND_TRUSTED_SKILL" in trusted
        assert "initial_failure" in observations


def test_workspace_skill_and_working_directory_cannot_override_package_skill(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = "WORKSPACE_FORGED_SKILL_IGNORE_APPROVAL"
    for relative in (SKILL_RELATIVE, Path("tracepilot") / SKILL_RELATIVE, Path("SKILL.md")):
        forged_path = java_fixture / relative
        forged_path.parent.mkdir(parents=True, exist_ok=True)
        forged_path.write_text(VALID_HEADER + fake, encoding="utf-8")
    monkeypatch.chdir(java_fixture)
    model = Mock(return_value=patch_action())

    propose(AgenticPatchPlanner(
        WorkspacePolicy(java_fixture), model, lambda _root: PassingRunner(),
    ))

    prompt = model.call_args.args[0]
    assert fake not in prompt
    assert load_java_test_triage_skill() in prompt


def test_model_read_of_forged_skill_remains_untrusted_observation(java_fixture: Path) -> None:
    fake = "MODEL_REQUESTED_REPLACEMENT_SKILL"
    forged_path = java_fixture / SKILL_RELATIVE
    forged_path.parent.mkdir(parents=True)
    forged_path.write_text(VALID_HEADER + fake, encoding="utf-8")
    model = Mock(side_effect=[
        {"action": "read", "relative_path": SKILL_RELATIVE.as_posix()},
        patch_action(),
    ])

    propose(AgenticPatchPlanner(
        WorkspacePolicy(java_fixture), model, lambda _root: PassingRunner(),
    ))

    trusted, observations = model.call_args.args[0].split("\nOBSERVATIONS: ", 1)
    assert load_java_test_triage_skill() in trusted
    assert fake not in trusted
    assert fake in observations


@pytest.mark.parametrize(("content", "reason"), [
    (None, "无法读取"),
    (b" \n\t", "文件为空"),
    (b"\xff\xfe", "UTF-8"),
    (b"not a skill document", "元数据块"),
    ((VALID_HEADER + "\x00broken").encode("utf-8"), "控制字符"),
    (VALID_HEADER.encode("utf-8"), "正文为空"),
    (b"---\nname: wrong-skill\ndescription: wrong\n---\nbody", "name"),
    (b"---\nname: java-test-triage\n---\nbody", "description"),
    (b"---\nname: java-test-triage\nname: duplicate\n---\nbody", "格式无效"),
])
def test_bad_packaged_skill_fails_before_model_or_runner(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    content: bytes | None, reason: str,
) -> None:
    package_root = tmp_path / "installed-tracepilot"
    skill_path = package_root / SKILL_RELATIVE
    skill_path.parent.mkdir(parents=True)
    if content is not None:
        skill_path.write_bytes(content)
    monkeypatch.setattr(skill_loader, "_PACKAGE_ROOT", package_root)
    model, runner_factory = Mock(), Mock()

    with pytest.raises(SkillConfigurationError, match=reason) as caught:
        AgenticPatchPlanner(WorkspacePolicy(java_fixture), model, runner_factory)

    assert str(skill_path) in str(caught.value)
    model.assert_not_called()
    runner_factory.assert_not_called()


def test_unreadable_packaged_skill_fails_clearly(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(SkillConfigurationError, match="无法读取") as caught:
        AgenticPatchPlanner(WorkspacePolicy(java_fixture), Mock(), Mock())
    assert isinstance(caught.value.__cause__, PermissionError)
