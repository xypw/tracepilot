"""Bounded offline acceptance against disposable, bundled Java fixtures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter

from fastapi.testclient import TestClient

from tracepilot.api import create_app
from tracepilot.factory import build_offline_workflow
from tracepilot.skill_loader import load_java_test_triage_skill
import tracepilot.skill_loader as skill_loader


SOURCE = "src/main/java/demo/CustomerService.java"
REQUEST = {
    "failure_text": "java.lang.NullPointerException\nat demo.CustomerService.displayName(CustomerService.java:5)",
    "test_target": "demo.CustomerServiceTest",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = perf_counter()
    checks: dict[str, bool] = {}
    outcomes: dict[str, str] = {}
    fixture = args.fixtures / "null-customer-name"
    fixture_before = {
        p.relative_to(fixture).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in fixture.rglob("*.java")
    }

    def check(name: str, condition: bool) -> None:
        checks[name] = bool(condition)

    with tempfile.TemporaryDirectory(prefix="tracepilot-deployment-acceptance-") as temporary:
        temp = Path(temporary)

        def build(name: str, *, fresh: bool = True):
            root = temp / name / "workspace"
            if fresh:
                shutil.copytree(fixture, root)
            workflow = build_offline_workflow(
                root, state_dir=temp / name / "state", trusted_local_runner=True,
            )
            return root, workflow

        root, workflow = build("approval")
        source = root / SOURCE
        original = source.read_bytes()
        with TestClient(create_app(workflow)) as client:
            started_response = client.post("/runs", json=REQUEST)
            waiting = started_response.json()
            check("start_http_200", started_response.status_code == 200)
            check("waiting_for_approval", waiting["status"] == "WAITING_APPROVAL")
            check("baseline_java_failed", not waiting["baseline_test_result"]["passed"])
            check("localized_expected_file", SOURCE in waiting["candidate_files"])
            check("proposed_expected_file", waiting["proposal"]["relative_path"] == SOURCE)
            check("unchanged_before_confirmation", source.read_bytes() == original)
            endpoint = f"/runs/{waiting['run_id']}/approval"
            wrong = client.post(endpoint, json={"approved": True, "proposal_digest": "0" * 64})
            check("wrong_digest_http_409", wrong.status_code == 409)
            check("wrong_digest_did_not_write", source.read_bytes() == original)
        workflow.close()

        _, restored = build("approval", fresh=False)
        with TestClient(create_app(restored)) as client:
            recovered = client.get(f"/runs/{waiting['run_id']}").json()
            check("service_object_checkpoint_recovered", recovered["status"] == "WAITING_APPROVAL")
            decision = {"approved": True, "proposal_digest": waiting["proposal"]["proposal_digest"]}
            completed_response = client.post(endpoint, json=decision)
            completed = completed_response.json()
            outcomes["approved"] = completed["status"]
            check("approval_http_200", completed_response.status_code == 200)
            check("task_succeeded", completed["status"] == "COMPLETED")
            check("targeted_java_test_passed", bool(completed["test_result"] and completed["test_result"]["passed"]))
            check("approved_source_changed", source.read_bytes() != original)
            after = source.read_bytes()
            calls = []
            real_run = restored.applier.runner.run

            def counted_run(target):
                calls.append(target)
                return real_run(target)

            restored.applier.runner.run = counted_run
            duplicate = client.post(endpoint, json=decision)
            check("duplicate_confirmation_same_result", duplicate.json() == completed)
            check("duplicate_confirmation_did_not_write", source.read_bytes() == after)
            check("duplicate_confirmation_did_not_rerun_java", not calls)
            conflict = client.post(endpoint, json={**decision, "approved": False})
            check("conflicting_decision_http_409", conflict.status_code == 409)
        restored.close()

        root, workflow = build("rejection")
        source = root / SOURCE
        original = source.read_bytes()
        with TestClient(create_app(workflow)) as client:
            waiting = client.post("/runs", json=REQUEST).json()
            canceled = client.post(f"/runs/{waiting['run_id']}/approval", json={
                "approved": False, "proposal_digest": waiting["proposal"]["proposal_digest"],
            }).json()
            outcomes["rejected"] = canceled["status"]
            check("rejection_canceled", canceled["status"] == "CANCELED")
            check("rejection_did_not_write", source.read_bytes() == original)
        workflow.close()

        root, workflow = build("stale")
        source = root / SOURCE
        with TestClient(create_app(workflow)) as client:
            waiting = client.post("/runs", json=REQUEST).json()
            changed = source.read_bytes() + b"\n// Synthetic external edit, isolated acceptance fixture only.\n"
            source.write_bytes(changed)
            calls = []
            real_run = workflow.applier.runner.run

            def counted_stale_run(target):
                calls.append(target)
                return real_run(target)

            workflow.applier.runner.run = counted_stale_run
            endpoint = f"/runs/{waiting['run_id']}/approval"
            decision = {"approved": True, "proposal_digest": waiting["proposal"]["proposal_digest"]}
            stale = client.post(endpoint, json=decision).json()
            outcomes["stale_confirmation"] = stale["status"]
            check("stale_confirmation_stopped", stale["status"] == "RESULT_UNKNOWN")
            check("stale_confirmation_explained", "确认失效" in (stale["error"] or ""))
            check("stale_confirmation_preserved_external_edit", source.read_bytes() == changed)
            check("stale_confirmation_did_not_run_java", not calls)
            repeated = client.post(endpoint, json=decision).json()
            check("stale_confirmation_did_not_retry", repeated == stale and not calls)
        workflow.close()

    check("original_fixture_unchanged", fixture_before == {
        p.relative_to(fixture).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in fixture.rglob("*.java")
    })
    body = load_java_test_triage_skill()
    check("packaged_skill_loads", "证据不足时停止，不猜测修复" in body)
    skill_path = Path(skill_loader.__file__).parent / "skills/java-test-triage/SKILL.md"
    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "offline_rule_based_planner",
            "scenario_count": 3,
            "recovery_scope": "service_object_recreated_from_sqlite",
            "external_model_calls": 0,
            "packaged_skill_sha256": hashlib.sha256(skill_path.read_bytes()).hexdigest(),
        },
        "checks": checks,
        "outcomes": outcomes,
        "passed": sum(checks.values()),
        "failed": len(checks) - sum(checks.values()),
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "failed", "latency_ms", "outcomes")}, ensure_ascii=False))
    return int(report["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
