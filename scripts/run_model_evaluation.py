"""在固定 Java 故障集上评测真实模型补丁规划，不记录 API Key。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter

from tracepilot.factory import build_model_workflow
from tracepilot.models import ApprovalRequest, RepairRequest, RunStatus
from tracepilot.security import WorkspacePolicy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def describe_error(error: Exception) -> str:
    """保留可诊断信息，同时移除 Windows 用户目录等本机路径。"""
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return f"{type(error).__name__}: HTTP {status_code}"
    message = str(error).replace(tempfile.gettempdir(), "<temporary-directory>")
    return f"{type(error).__name__}: {message[:500]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行真实模型 Java 修复评测")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="TRACEPILOT_MODEL_API_KEY")
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "evaluation_data" / "cases.json")
    parser.add_argument("--fixtures", type=Path, default=PROJECT_ROOT / "evaluation_fixtures")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "model-evaluation.json")
    parser.add_argument("--java", default="java")
    parser.add_argument("--javac", default="javac")
    parser.add_argument("--limit", type=int, default=12)
    return parser.parse_args()


def evaluate_case(case: dict[str, str], args: argparse.Namespace, api_key: str) -> dict[str, object]:
    started = perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix=f"tracepilot-model-{case['case_id']}-") as temp:
            root = Path(temp) / "workspace"
            state = Path(temp) / "state"
            shutil.copytree(args.fixtures / case["fixture"], root)
            policy = WorkspacePolicy(root)
            before = policy.read_text(case["expected_file"])
            workflow, caller = build_model_workflow(
                root,
                state_dir=state,
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                java_executable=args.java,
                javac_executable=args.javac,
            )
            try:
                waiting = workflow.start(
                    RepairRequest(
                        failure_text=case["failure_text"],
                        test_target=case["test_target"],
                    ),
                    run_id=f"model-{case['case_id']}",
                )
                unchanged = policy.read_text(case["expected_file"]) == before
                proposal = waiting.proposal
                if waiting.status != RunStatus.WAITING_APPROVAL or proposal is None:
                    raise RuntimeError(f"未进入等待确认状态: {waiting.status}")
                completed = workflow.resume(
                    waiting.run_id,
                    ApprovalRequest(
                        approved=True,
                        proposal_digest=proposal.proposal_digest,
                    ),
                )
                result = {
                    "case_id": case["case_id"],
                    "status": completed.status.value,
                    "localized_expected_file": case["expected_file"] in waiting.candidate_files,
                    "proposed_expected_file": proposal.relative_path == case["expected_file"],
                    "unchanged_before_confirmation": unchanged,
                    "task_succeeded": completed.status == RunStatus.COMPLETED,
                    "targeted_test_passed": bool(completed.test_result and completed.test_result.passed),
                    "error": completed.error,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                }
            finally:
                # Windows 不允许删除仍被 SQLite/HTTP 客户端占用的临时目录。
                # 必须在 TemporaryDirectory 退出前释放两个资源。
                workflow.close()
                caller.close()
            return result
    except Exception as error:
        return {
            "case_id": case["case_id"],
            "status": "EVALUATION_ERROR",
            "localized_expected_file": False,
            "proposed_expected_file": False,
            "unchanged_before_confirmation": True,
            "task_succeeded": False,
            "targeted_test_passed": False,
            "error": describe_error(error),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }


def main() -> int:
    args = parse_args()
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"环境变量 {args.api_key_env} 未配置")
    all_cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = all_cases[: args.limit]
    results = [evaluate_case(case, args, api_key) for case in cases]
    total = len(results)
    valid_results = [item for item in results if item["status"] != "EVALUATION_ERROR"]
    valid_total = len(valid_results)

    def rate(field: str) -> float | None:
        if not valid_total:
            return None
        return round(sum(bool(item[field]) for item in valid_results) / valid_total, 4)

    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "openai_compatible_model_planner",
            "model": args.model,
            "base_url": args.base_url,
            "case_count": total,
            "valid_case_count": valid_total,
            "evaluation_error_count": total - valid_total,
            "cases_file": args.cases.name,
            "api_key_recorded": False,
        },
        "metrics": {
            "file_localization_accuracy": rate("localized_expected_file"),
            "proposal_file_accuracy": rate("proposed_expected_file"),
            "confirmation_block_rate": rate("unchanged_before_confirmation"),
            "task_success_rate": rate("task_succeeded"),
            "targeted_test_pass_rate": rate("targeted_test_passed"),
            "average_latency_ms": round(
                sum(float(item["latency_ms"]) for item in valid_results) / valid_total, 2
            ) if valid_total else None,
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"报告已写入: {args.output.resolve()}")
    return 0 if valid_total == total and all(item["task_succeeded"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
