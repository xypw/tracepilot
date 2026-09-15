from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tracepilot.api import create_app
from tracepilot.factory import build_offline_workflow


FAILURE = "at demo.CustomerService.displayName(CustomerService.java:5)"


def test_api_start_status_and_approve(java_fixture: Path, tmp_path: Path) -> None:
    workflow = build_offline_workflow(
        java_fixture,
        state_dir=tmp_path / "state",
        java_executable=r"D:\jdk\bin\java.exe",
        javac_executable=r"D:\jdk\bin\javac.exe",
    )
    with TestClient(create_app(workflow)) as client:
        started = client.post(
            "/runs",
            json={"failure_text": FAILURE, "test_target": "demo.CustomerServiceTest"},
        )
        assert started.status_code == 200
        body = started.json()
        assert body["status"] == "WAITING_APPROVAL"
        queried = client.get(f"/runs/{body['run_id']}")
        assert queried.status_code == 200
        approved = client.post(
            f"/runs/{body['run_id']}/approval",
            json={
                "approved": True,
                "proposal_digest": body["proposal"]["proposal_digest"],
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "COMPLETED"
    workflow.close()


def test_api_returns_404_for_unknown_run(java_fixture: Path, tmp_path: Path) -> None:
    workflow = build_offline_workflow(java_fixture, state_dir=tmp_path / "state")
    with TestClient(create_app(workflow)) as client:
        response = client.get("/runs/missing")
        assert response.status_code == 404
    workflow.close()
