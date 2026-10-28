"""Read-only native lifecycle follow-up against a completed synthetic baseline."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from dotenv import dotenv_values
import httpx

from evaluate_memory import CASES, HERE, EvaluationStopped, Runner, load_cases, projected_result

MAX_REQUESTS = 20
MAX_SEARCHES = 9
EXTRA_FIELDS = ("lifecycle_state", "replaced_by", "synthesized", "linked_memory_ids", "structured_attributes")


def prepare(source):
    data = json.loads(source.read_text())
    if data.get("evidence") != "live_provider" or data.get("status") not in {
        "reviewed_retrieval_baseline", "completed_pending_semantic_review"
    } or data.get("cases_sha256") != hashlib.sha256(CASES.read_bytes()).hexdigest():
        raise EvaluationStopped("A completed live baseline with unchanged fixtures is required.")
    cases = load_cases()
    selected = [case for case in cases["queries"] if case["kind"] == "changed"]
    pairs = {}
    snapshot = {row["id"] for row in data["snapshots"]["changes"]["results"]}
    for case in selected:
        captured = next(row for row in data["queries"] if row["case_id"] == case["id"])
        rank = captured.get("expected_anchor_rank")
        if type(rank) is not int or rank <= 1:
            raise EvaluationStopped("Expected the captured old-first correction baseline.")
        old_id, new_id = captured["results"][0]["id"], captured["results"][rank - 1]["id"]
        if any(not re.fullmatch(r"[a-f0-9-]{36}", value) or value not in snapshot for value in (old_id, new_id)):
            raise EvaluationStopped("Correction IDs must belong to the captured synthetic snapshot.")
        pairs[case["id"]] = {"old_id": old_id, "new_id": new_id}
    return data, selected, pairs


class CorrectionRunner(Runner):
    def request(self, operation, method, path, **kwargs):
        payload = kwargs.get("json", {})
        is_detail = operation == "detail" and method == "GET" and re.fullmatch(r"/v1/memories/[a-f0-9-]{36}/", path)
        is_list = operation == "list" and method == "POST" and path == "/v3/memories/"
        is_search = operation == "search" and method == "POST" and path == "/v3/memories/search/"
        if not (is_detail or is_list or is_search):
            raise EvaluationStopped("Follow-up permits only memory reads; writes and settings changes are prohibited.")
        if (is_list or is_search) and payload.get("filters") != {"user_id": self.owner}:
            raise EvaluationStopped("Follow-up reads must use the isolated baseline owner.")
        if len(self.report["requests"]) >= MAX_REQUESTS or (is_search and sum(row["operation"] == "search" for row in self.report["requests"]) >= MAX_SEARCHES):
            raise EvaluationStopped("Follow-up request/search cap reached.")
        return super().request(operation, method, path, **kwargs)

    def rows(self, data, *, maximum=None):
        rows = super().rows(data, maximum=maximum)
        for safe, original in zip(rows, data["results"]):
            safe.update({key: original[key] for key in EXTRA_FIELDS if key in original})
        return rows

    def search_case(self, case, *, latest_only, pair=None):
        payload = {"query": case["query"], "filters": {"user_id": self.owner}, "top_k": 5}
        if latest_only:
            payload["latest_only"] = True
        response = self.request("search", "POST", "/v3/memories/search/", json=payload)
        rows = self.rows(response, maximum=5)
        result = projected_result(case, rows)
        result["mode"] = "latest_only" if latest_only else "provider_default"
        result["latency_ms"] = self.report["requests"][-1]["latency_ms"]
        if pair:
            result["captured_old_id_returned"] = any(row.get("id") == pair["old_id"] for row in rows)
            result["captured_new_id_rank"] = next((i + 1 for i, row in enumerate(rows) if row.get("id") == pair["new_id"]), None)
        self.report["queries"].append(result)
        self.save()

    def followup(self, baseline, cases, pairs):
        self.report.update(
            limits={"requests": MAX_REQUESTS, "searches": MAX_SEARCHES, "adds": 0},
            baseline_finished_at=baseline["finished_at"],
            elapsed_since_baseline_minutes=(datetime.now(timezone.utc) - datetime.fromisoformat(baseline["finished_at"])).total_seconds() / 60,
            protocol={"writes": 0, "paired_default_and_latest_only": 3, "latest_only_controls": 3},
            details={},
        )
        try:
            for case in cases:
                for label, identifier in pairs[case["id"]].items():
                    data = self.request("detail", "GET", f"/v1/memories/{identifier}/")
                    if data.get("id") != identifier or data.get("user_id") != self.owner:
                        raise EvaluationStopped("Memory detail owner/ID mismatch; follow-up stopped.")
                    row = self.rows({"results": [data]})[0]
                    row["available_fields"] = sorted(data)
                    self.report["details"][case["id"] + ":" + label] = row
                    self.save()
            self.snapshot("delayed_default")
            # Snapshot implementation is deliberately explicit here to vary only this flag.
            data = self.request("list", "POST", "/v3/memories/", params={"page": 1, "page_size": 50},
                                json={"filters": {"user_id": self.owner}, "latest_only": True})
            active = self.rows(data, maximum=50)
            if type(data.get("count")) is not int or data.get("count") != len(active) or data.get("next"):
                raise EvaluationStopped("Active snapshot incomplete; not scored.")
            self.report["snapshots"]["latest_only"] = {"count": data["count"], "results": active}
            self.save()
            for case in cases:
                self.search_case(case, latest_only=False, pair=pairs[case["id"]])
                self.search_case(case, latest_only=True, pair=pairs[case["id"]])
            controls = [row for row in load_cases()["queries"] if row["id"] in {
                "query-language", "query-unknown-dog", "query-unknown-allergy"
            }]
            for case in controls:
                self.search_case(case, latest_only=True)
            self.report["status"] = "completed_pending_semantic_review"
        except (EvaluationStopped, KeyboardInterrupt) as error:
            self.report["status"] = "incomplete"
            self.report["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else "Interrupted; no retry made."
            raise
        finally:
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allowance-confirmed", action="store_true")
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"mode": "plan_only", "network_requests": 0, "writes": 0,
                          "maximum_requests": MAX_REQUESTS, "searches": MAX_SEARCHES,
                          "cases": "3 default/latest-only correction pairs; Italian/dog/allergy controls",
                          "reads": "6 captured memory details and 2 owner-scoped snapshots",
                          "app_settings_changes": False}, indent=2))
        return
    if not args.allowance_confirmed or not args.baseline_report:
        parser.error("Live follow-up requires a current included-allowance check and --baseline-report.")
    source = args.baseline_report.resolve()
    if not source.is_relative_to((HERE / "output").resolve()):
        parser.error("Use an existing local synthetic report under output/.")
    baseline, cases, pairs = prepare(source)
    local = dotenv_values(HERE / ".env")
    key = os.environ.get("MEM0_API_KEY") or local.get("MEM0_API_KEY")
    if not key:
        parser.error("Missing MEM0_API_KEY")
    path = source.parent / ("native-corrections-" + uuid.uuid4().hex[:8] + ".json")
    try:
        with httpx.Client(base_url="https://api.mem0.ai", timeout=20, follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0), headers={"Authorization": "Token " + key}) as client:
            runner = CorrectionRunner(client, baseline["owner"], path)
            runner.report["baseline_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            runner.followup(baseline, cases, pairs)
        print(f"Read-only follow-up completed: {path}")
    except (EvaluationStopped, KeyboardInterrupt):
        raise SystemExit("Follow-up stopped; inspect its local report. No writes, retries or settings changes were made.") from None


if __name__ == "__main__":
    main()
