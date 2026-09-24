"""Verify a localhost offline demo against an expendable fixture copy, without a model call."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--allow-demo-write", action="store_true", required=True)
    args = parser.parse_args()
    case = json.loads((Path(__file__).resolve().parents[1] / "evaluation_data/cases.json").read_text(encoding="utf-8"))[0]
    source = args.workspace / case["expected_file"]
    original = sha256(source.read_bytes()).hexdigest()
    with httpx.Client(base_url="http://127.0.0.1:8020", trust_env=False, timeout=90) as client:
        for path in ("/health", "/demo", "/openapi.json"):
            assert client.get(path).status_code == 200, path
        response = client.post("/runs", json={k: case[k] for k in ("failure_text", "test_target")})
        response.raise_for_status()
        waiting = response.json()
        assert waiting["status"] == "WAITING_APPROVAL", waiting["status"]
        assert not waiting["baseline_test_result"]["passed"]
        assert sha256(source.read_bytes()).hexdigest() == original
        endpoint = "/runs/" + waiting["run_id"] + "/approval"
        assert client.post(endpoint, json={"approved": True, "proposal_digest": "0" * 64}).status_code == 409
        assert sha256(source.read_bytes()).hexdigest() == original
        payload = {"approved": True, "proposal_digest": waiting["proposal"]["proposal_digest"]}
        completed_response = client.post(endpoint, json=payload)
        completed_response.raise_for_status()
        completed = completed_response.json()
        assert completed["status"] == "COMPLETED"
        assert completed["test_result"]["passed"]
        written = source.stat().st_mtime_ns
        duplicate = client.post(endpoint, json=payload)
        duplicate.raise_for_status()
        assert duplicate.json() == completed
        assert source.stat().st_mtime_ns == written
        assert client.get("/runs/" + waiting["run_id"]).json()["status"] == "COMPLETED"
    print(json.dumps({"mode": "deployed_offline_planner_real_java", "external_model_requests": 0,
                      "checks": {"pages_and_health": "passed", "baseline_failure_reproduced": True,
                                 "no_write_before_approval": True, "wrong_digest_rejected": True,
                                 "approved_patch_target_test_passed": True, "duplicate_approval_no_rewrite": True,
                                 "saved_result_readable": True}}, indent=2))


if __name__ == "__main__":
    main()
