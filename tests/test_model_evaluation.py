from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tempfile

import httpx

from scripts import run_model_evaluation
from tracepilot.factory import build_offline_workflow


class _DummyCaller:
    def close(self) -> None:
        pass


def test_evaluation_closes_sqlite_before_temporary_directory_cleanup(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    case = json.loads(
        (project_root / "evaluation_data" / "cases.json").read_text(encoding="utf-8")
    )[0]

    def build_without_external_model(workspace_root, *, state_dir, **_kwargs):
        workflow = build_offline_workflow(
            workspace_root,
            state_dir=state_dir,
            java_executable="java",
            javac_executable="javac",
        )
        return workflow, _DummyCaller()

    monkeypatch.setattr(run_model_evaluation, "build_model_workflow", build_without_external_model)
    args = Namespace(
        fixtures=project_root / "evaluation_fixtures",
        base_url="https://example.invalid/v1",
        model="test-model",
        java="java",
        javac="javac",
    )

    result = run_model_evaluation.evaluate_case(case, args, "not-a-real-key")

    assert result["status"] == "COMPLETED"
    assert result["task_succeeded"] is True
    assert "PermissionError" not in str(result["error"])


def test_error_description_redacts_local_temp_path() -> None:
    error = PermissionError(f"cannot remove {tempfile.gettempdir()}\\private.sqlite3")

    description = run_model_evaluation.describe_error(error)

    assert tempfile.gettempdir() not in description
    assert "<temporary-directory>" in description


def test_error_description_keeps_only_http_status() -> None:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(429, request=request)
    error = httpx.HTTPStatusError("quota", request=request, response=response)

    assert run_model_evaluation.describe_error(error) == "HTTPStatusError: HTTP 429"
