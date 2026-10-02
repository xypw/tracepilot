"""The model path must not execute candidate Java code on the host."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from tracepilot.factory import build_model_workflow, build_offline_workflow
from tracepilot.models import RepairRequest, RunStatus
from tracepilot.sandbox import DockerJavacSandboxRunner, SandboxUnavailable


def test_sandbox_stages_only_java_and_applies_limits(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (java_fixture / ".env").write_text("PRIVATE_TOKEN=do-not-copy", encoding="utf-8")
    (java_fixture / "RootExtra.java").write_text("class RootExtra {}", encoding="utf-8")
    (java_fixture / "src" / "main" / "java" / "private.properties").write_text(
        "secret=do-not-copy", encoding="utf-8"
    )

    def fake_run(_runner, command, _cid_file):
        assert command[:2] == ["docker", "run"]
        for flag in (
            "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--memory-swap=512m", "--cpus=1",
            "--user=65534:65534", "--env=HOME=/tmp",
            "--log-driver=none",
        ):
            assert flag in command
        assert not any("docker.sock" in part for part in command)
        source_mount = next(
            part for part in command if part.startswith("type=bind,")
            and "target=/workspace" in part
        )
        source_root = Path(source_mount.split("source=", 1)[1].split(",target=", 1)[0])
        staged = [item.relative_to(source_root).as_posix() for item in source_root.rglob("*")
                  if item.is_file()]
        assert staged
        assert all(item.endswith(".java") for item in staged)
        assert all(item.startswith(("src/main/java/", "src/test/java/")) for item in staged)
        assert not any(".env" in item or "properties" in item for item in staged)
        assert source_mount.endswith(",readonly")
        Path(command[command.index("--cidfile") + 1]).write_text("a" * 64)
        return 0, "targeted test passed"

    monkeypatch.setattr(DockerJavacSandboxRunner, "_run_bounded", fake_run)
    result = DockerJavacSandboxRunner(java_fixture).run("demo.CustomerServiceTest")

    assert result.passed
    assert result.output_tail.strip() == "targeted test passed"


def test_sandbox_unavailable_never_falls_back_to_host(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_docker(_command, **_kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "Popen", missing_docker)
    with pytest.raises(SandboxUnavailable, match="拒绝在宿主机回退"):
        DockerJavacSandboxRunner(java_fixture).run("demo.CustomerServiceTest")


def test_sandbox_timeout_stops_only_its_own_container(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = []

    def stop_container(command, **_kwargs):
        stopped.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", stop_container)
    cid_file = tmp_path / "timeout.cid"
    cid_file.write_text("b" * 64)
    runner = DockerJavacSandboxRunner(java_fixture, timeout_seconds=0.1)
    with pytest.raises(SandboxUnavailable, match="测试超时，容器已停止"):
        runner._run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], cid_file)
    assert stopped == [["docker", "rm", "-f", "b" * 64]]


def test_invalid_test_target_is_rejected_before_docker(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_run(_command, **_kwargs):
        raise AssertionError("Docker should not be called")

    monkeypatch.setattr(subprocess, "Popen", unexpected_run)
    with pytest.raises(ValueError, match="白名单格式"):
        DockerJavacSandboxRunner(java_fixture).run("demo.Test; rm -rf /tmp")


def test_default_offline_workflow_fails_closed_without_sandbox(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = build_offline_workflow(java_fixture, state_dir=tmp_path / "state")
    assert isinstance(workflow.applier.runner, DockerJavacSandboxRunner)
    before = (java_fixture / "src/main/java/demo/CustomerService.java").read_bytes()

    def unavailable(_target):
        raise SandboxUnavailable("Docker unavailable")

    monkeypatch.setattr(workflow.applier.runner, "run", unavailable)
    try:
        result = workflow.start(RepairRequest(
            failure_text="at demo.CustomerService.displayName(CustomerService.java:5)",
            test_target="demo.CustomerServiceTest",
        ))
        assert result.status == RunStatus.FAILED
        assert result.error == "SANDBOX_UNAVAILABLE"
        assert (java_fixture / "src/main/java/demo/CustomerService.java").read_bytes() == before
    finally:
        workflow.close()


def test_model_workflow_uses_sandbox_for_baseline_and_trial(
    java_fixture: Path, tmp_path: Path,
) -> None:
    workflow, caller = build_model_workflow(
        java_fixture,
        state_dir=tmp_path / "state",
        base_url="https://example.invalid/v1",
        api_key="not-a-real-key",
        model="test-model",
    )
    try:
        assert isinstance(workflow.applier.runner, DockerJavacSandboxRunner)
        trial_runner = workflow.planner.runner_factory(java_fixture)
        assert isinstance(trial_runner, DockerJavacSandboxRunner)
    finally:
        workflow.close()
        caller.close()


def test_output_budget_stops_exact_container(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = []

    def stop_container(command, **_kwargs):
        stopped.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", stop_container)
    cid_file = tmp_path / "output.cid"
    cid_file.write_text("c" * 64)
    runner = DockerJavacSandboxRunner(java_fixture, max_output_bytes=32_768)
    with pytest.raises(SandboxUnavailable, match="输出超过总预算，容器已停止"):
        runner._run_bounded(
            [sys.executable, "-c", "import os;\nwhile True: os.write(1, b'x' * 8192)"],
            cid_file,
        )
    assert stopped == [["docker", "rm", "-f", "c" * 64]]


def test_output_tail_is_bounded_and_includes_stderr(java_fixture: Path, tmp_path: Path) -> None:
    runner = DockerJavacSandboxRunner(java_fixture)
    return_code, output = runner._run_bounded(
        [sys.executable, "-c", "import os; os.write(1, b'x' * 50_000); os.write(2, b'final-error')"],
        tmp_path / "unused.cid",
    )
    assert return_code == 0
    assert len(output.encode("utf-8")) == 4000
    assert output.endswith("final-error")


@pytest.mark.parametrize("return_code", [125, 126, 127])
def test_docker_start_errors_are_not_java_test_failures(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch, return_code: int,
) -> None:
    def failed_start(_runner, _command, cid_file):
        cid_file.write_text("d" * 64)
        return return_code, "container start failed"

    monkeypatch.setattr(DockerJavacSandboxRunner, "_run_bounded", failed_start)
    with pytest.raises(SandboxUnavailable, match="受限容器未能启动"):
        DockerJavacSandboxRunner(java_fixture).run("demo.CustomerServiceTest")


def test_missing_cid_is_not_reported_as_a_pass(
    java_fixture: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DockerJavacSandboxRunner, "_run_bounded", lambda *_: (0, ""))
    with pytest.raises(SandboxUnavailable, match="受限容器未能启动"):
        DockerJavacSandboxRunner(java_fixture).run("demo.CustomerServiceTest")


def test_cleanup_accepts_an_already_removed_container(
    java_fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid_file = tmp_path / "removed.cid"
    cid_file.write_text("e" * 64)
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **_: subprocess.CompletedProcess(command, 1, "", "Error: No such container"),
    )
    DockerJavacSandboxRunner(java_fixture)._stop_container(cid_file)
