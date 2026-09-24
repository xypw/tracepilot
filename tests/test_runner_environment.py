"""验证测试执行器的固定命令和环境变量边界，不宣称本机进程是沙箱。"""
from unittest.mock import patch
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
