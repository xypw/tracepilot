"""Windows junctions exercise reparse checks without symlink privileges."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tracepilot.sandbox import DockerJavacSandboxRunner
from tracepilot.security import WorkspacePolicy, WorkspaceViolation
from tracepilot.tools import CodeTools, JavacMainTestRunner


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction acceptance")
@pytest.mark.parametrize("relative", ["src/main/java", "src/main/java/linked"])
def test_windows_junction_is_rejected_before_either_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "External.java").write_text("class External {} // unique-junction-marker", encoding="utf-8")
    link = root / relative
    link.parent.mkdir(parents=True)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "New-Item -ItemType Junction -Path $env:TP_TEST_LINK -Target $env:TP_TEST_TARGET -ErrorAction Stop | Out-Null"],
        env={**os.environ, "TP_TEST_LINK": str(link), "TP_TEST_TARGET": str(outside)},
        capture_output=True, timeout=20, shell=False,
    )
    if result.returncode:
        pytest.skip("Environment cannot create a Windows junction")
    try:
        assert link.lstat().st_file_attributes & 1024
        policy = WorkspacePolicy(root)
        with pytest.raises(WorkspaceViolation, match="重解析点"):
            policy.read_text(relative + "/External.java")
        assert CodeTools(policy).search("unique-junction-marker") == []

        def unexpected_execution(*_args, **_kwargs):
            raise AssertionError("Rejected source must never start Java or Docker")

        monkeypatch.setattr(subprocess, "run", unexpected_execution)
        monkeypatch.setattr(DockerJavacSandboxRunner, "_run_bounded", unexpected_execution)
        for runner in (JavacMainTestRunner(root), DockerJavacSandboxRunner(root)):
            with pytest.raises(WorkspaceViolation, match="重解析点"):
                runner.run("External")
        assert (outside / "External.java").is_file()
    finally:
        # Remove only the junction entry, never recursively walk its target.
        link.rmdir()
