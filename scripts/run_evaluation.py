"""在固定 Java 故障集上运行 TracePilot 离线端到端评测。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter

from tracepilot.factory import build_offline_workflow
from tracepilot.models import ApprovalRequest, RepairRequest, RunStatus
from tracepilot.security import WorkspacePolicy
from tracepilot.tools import JavacMainTestRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行固定 Java 故障集评测")
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evaluation_data" / "cases.json",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=PROJECT_ROOT / "evaluation_fixtures",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "offline-evaluation.json",
    )
    parser.add_argument("--java", default="java")
    parser.add_argument("--javac", default="javac")
    return parser.parse_args()


def evaluate_case(case: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"tracepilot-{case['case_id']}-") as temp:
        root = Path(temp) / "workspace"
        state_dir = Path(temp) / "state"
        shutil.copytree(args.fixtures / case["fixture"], root)
        policy = WorkspacePolicy(root)
        expected_before = policy.read_text(case["expected_file"])
        runner = JavacMainTestRunner(
            root,
            java_executable=args.java,
            javac_executable=args.javac,
        )
        baseline = runner.run(case["test_target"])

        first = build_offline_workflow(
            root,
            state_dir=state_dir,
            java_executable=args.java,
            javac_executable=args.javac,
        )
        waiting = first.start(
            RepairRequest(
                failure_text=case["failure_text"],
                test_target=case["test_target"],
            ),
            run_id=f"eval-{case['case_id']}",
        )
        unchanged_before_confirmation = (
            policy.read_text(case["expected_file"]) == expected_before
        )
        first.close()

        recovered = build_offline_workflow(
            root,
            state_dir=state_dir,
            java_executable=args.java,
            javac_executable=args.javac,
        )
        restored = recovered.get(waiting.run_id)
        proposal = restored.proposal
        if proposal is None:
            raise RuntimeError("工作流未产生补丁提案")
        completed = recovered.resume(
            restored.run_id,
            ApprovalRequest(
                approved=True,
                proposal_digest=proposal.proposal_digest,
            ),
        )
        content_after_first_confirmation = policy.read_text(case["expected_file"])
        repeated = recovered.resume(
            restored.run_id,
            ApprovalRequest(
                approved=True,
                proposal_digest=proposal.proposal_digest,
            ),
        )
        duplicate_confirmation_idempotent = (
            content_after_first_confirmation
            == policy.read_text(case["expected_file"])
            and repeated.status == RunStatus.COMPLETED
        )
        recovered.close()

        return {
            "case_id": case["case_id"],
            "baseline_failed": not baseline.passed,
            "localized_expected_file": case["expected_file"] in waiting.candidate_files,
            "proposed_expected_file": proposal.relative_path == case["expected_file"],
            "unchanged_before_confirmation": unchanged_before_confirmation,
            "checkpoint_recovered": restored.status == RunStatus.WAITING_APPROVAL,
            "task_succeeded": completed.status == RunStatus.COMPLETED,
            "targeted_test_passed": bool(completed.test_result and completed.test_result.passed),
            "duplicate_confirmation_idempotent": duplicate_confirmation_idempotent,
            "tool_trace": [item.model_dump(mode="json") for item in completed.trace],
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }


def main() -> int:
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    results = [evaluate_case(case, args) for case in cases]
    total = len(results)

    def rate(field: str) -> float:
        return round(sum(bool(item[field]) for item in results) / total, 4) if total else 0.0

    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "offline_rule_based_planner",
            "case_count": total,
            "cases_file": args.cases.name,
            "notes": [
                "固定评测使用确定性规则规划器，不代表真实大模型修复成功率。",
                "每个案例从原始夹具复制到临时目录，评测不会修改样例源文件。",
            ],
        },
        "metrics": {
            "baseline_failure_rate": rate("baseline_failed"),
            "file_localization_accuracy": rate("localized_expected_file"),
            "proposal_file_accuracy": rate("proposed_expected_file"),
            "confirmation_block_rate": rate("unchanged_before_confirmation"),
            "checkpoint_recovery_rate": rate("checkpoint_recovered"),
            "task_success_rate": rate("task_succeeded"),
            "targeted_test_pass_rate": rate("targeted_test_passed"),
            "duplicate_confirmation_idempotency_rate": rate(
                "duplicate_confirmation_idempotent"
            ),
            "average_latency_ms": round(
                sum(float(item["latency_ms"]) for item in results) / total, 2
            ) if total else 0.0,
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"报告已写入: {args.output.resolve()}")
    return 0 if all(item["task_succeeded"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
