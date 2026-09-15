"""TracePilot FastAPI接口。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from tracepilot.models import ApprovalRequest, RepairRequest, RepairRunResponse
from tracepilot.workflow import RepairWorkflow


def create_app(workflow: RepairWorkflow) -> FastAPI:
    app = FastAPI(title="TracePilot", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs", response_model=RepairRunResponse)
    def start_run(request: RepairRequest) -> RepairRunResponse:
        try:
            return workflow.start(request)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/runs/{run_id}", response_model=RepairRunResponse)
    def get_run(run_id: str) -> RepairRunResponse:
        try:
            return workflow.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run不存在") from error

    @app.post("/runs/{run_id}/approval", response_model=RepairRunResponse)
    def approve_run(run_id: str, request: ApprovalRequest) -> RepairRunResponse:
        try:
            return workflow.resume(run_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run不存在") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app

