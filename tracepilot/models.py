"""TracePilot 的输入、状态和工具结果契约。"""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    DIAGNOSING = "DIAGNOSING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    failure_text: str = Field(min_length=1, max_length=20_000)
    test_target: str = Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_.$]*$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str
    line: int = Field(ge=1)
    excerpt: str


class TestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    passed: bool
    command: list[str]
    return_code: int
    output_tail: str


class PatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    proposal_id: str = Field(min_length=12)
    relative_path: str
    old_text: str = Field(min_length=1, max_length=4_000)
    new_text: str = Field(min_length=1, max_length=4_000)
    explanation: str = Field(min_length=1, max_length=1_000)
    test_target: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def create(
        cls,
        *,
        relative_path: str,
        old_text: str,
        new_text: str,
        explanation: str,
        test_target: str,
        source_sha256: str,
    ) -> "PatchProposal":
        payload = {
            "relative_path": relative_path,
            "old_text": old_text,
            "new_text": new_text,
            "explanation": explanation,
            "test_target": test_target,
            "source_sha256": source_sha256,
        }
        digest = sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cls(
            proposal_id=f"patch-{digest[:16]}",
            proposal_digest=digest,
            **payload,
        )


class ToolTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step: int = Field(ge=1)
    tool: str
    summary: str


class RepairRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    status: RunStatus
    candidate_files: list[str] = Field(default_factory=list)
    proposal: PatchProposal | None = None
    diff_preview: str | None = None
    baseline_test_result: TestResult | None = None
    test_result: TestResult | None = None
    trace: list[ToolTrace] = Field(default_factory=list)
    error: str | None = None
