"""验证测试执行器的固定命令和环境变量边界，不宣称本机进程是沙箱。"""
from unittest.mock import patch
from pathlib import Path
import subprocess

import pytest

from tracepilot.tools import JavacMainTestRunner, MavenTestRunner
from tracepilot.security import WorkspaceViolation


@pytest.mark.parametrize("kind", ["maven", "javac"])
def test_child_does_not_inherit_credentials_or_vm_options(java_fixture, kind):
    runner = MavenTestRunner(java_fixture) if kind == "maven" else JavacMainTestRunner(java_fixture)
    injected = {"MODEL_API_KEY": "test-secret-never-inherit", "JAVA_TOOL_OPTIONS": "-javaagent:bad.jar",
                "MAVEN_OPTS": "-Dsecret=true", "CLASSPATH": "evil.jar", "PATH": "test-path"}
    completed = subprocess.CompletedProcess([], 0, "passed", "")
    with patch.dict("os.environ", injected, clear=True), patch(
        "tracepilot.tools.subprocess.run", return_value=completed
    ) as run:
        assert runner.run("demo.CustomerServiceTest").passed
    assert run.call_count == (1 if kind == "maven" else 2)
    for call in run.call_args_list:
        assert call.kwargs["env"] == {"PATH": "test-path"}
        assert call.kwargs["shell"] is False
        assert isinstance(call.args[0], list)


@pytest.mark.parametrize("kind", ["maven", "javac"])
def test_rejects_shell_arguments_before_process_start(java_fixture, kind):
    runner = MavenTestRunner(java_fixture) if kind == "maven" else JavacMainTestRunner(java_fixture)
    with patch("tracepilot.tools.subprocess.run") as run:
        with pytest.raises(WorkspaceViolation):
            runner.run("demo.Test; curl attacker.invalid")
        run.assert_not_called()


def test_javac_uses_only_approved_sources_and_explicit_paths(java_fixture):
    (java_fixture / "RootExtra.java").write_text("class RootExtra {}", encoding="utf-8")
    (java_fixture / "target").mkdir()
    (java_fixture / "target/BuildExtra.java").write_text("class BuildExtra {}", encoding="utf-8")
    completed = subprocess.CompletedProcess([], 0, "passed", "")
    with patch("tracepilot.tools.subprocess.run", return_value=completed) as run:
        assert JavacMainTestRunner(java_fixture).run("demo.CustomerServiceTest").passed
    compile_command = run.call_args_list[0].args[0]
    sources = [Path(part).relative_to(java_fixture).as_posix()
               for part in compile_command if part.endswith(".java")]
    assert sources
    assert all(path.startswith(("src/main/java/", "src/test/java/")) for path in sources)
    output = compile_command[compile_command.index("-d") + 1]
    assert compile_command[compile_command.index("-classpath") + 1] == output
    assert compile_command[compile_command.index("-sourcepath") + 1] == ""
    assert "-implicit:none" in compile_command
    assert "-proc:none" in compile_command
    runtime = run.call_args_list[1].args[0]
    assert runtime[runtime.index("-cp") + 1] == output


@pytest.mark.parametrize("kind", ["file", "directory", "source_root"])
def test_javac_rejects_linked_sources_before_start(java_fixture, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "External.java").write_text("class External {}", encoding="utf-8")
    link = java_fixture / "src/main/java/demo/External.java"
    target = outside / "External.java"
    is_directory = kind != "file"
    if kind == "directory":
        link = java_fixture / "src/main/java/external"
        target = outside
    elif kind == "source_root":
        # A fresh workspace lets us link a source root without replacing fixture data.
        java_fixture = tmp_path / "linked-workspace"
        (java_fixture / "src/main").mkdir(parents=True)
        link = java_fixture / "src/main/java"
        target = outside
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as error:
        pytest.skip(f"Environment cannot create symlinks: {error}")
    with patch("tracepilot.tools.subprocess.run") as run:
        with pytest.raises(WorkspaceViolation):
            JavacMainTestRunner(java_fixture).run("demo.CustomerServiceTest")
        run.assert_not_called()


def test_javac_rejects_oversized_source_before_start(java_fixture):
    (java_fixture / "src/main/java/demo/Huge.java").write_text("x" * 200_001, encoding="utf-8")
    with patch("tracepilot.tools.subprocess.run") as run:
        with pytest.raises(WorkspaceViolation, match="大小"):
            JavacMainTestRunner(java_fixture).run("demo.CustomerServiceTest")
        run.assert_not_called()
