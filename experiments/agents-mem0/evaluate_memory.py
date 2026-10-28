"""Bounded, isolated Mem0 baseline. Default: print the plan, no credentials/network."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import time
from urllib.parse import quote
import uuid

import httpx
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
CASES = HERE / "evaluation" / "cases.json"
MAX_REQUESTS = 280
MAX_POLLS = 20
MAX_PAGES = 2
PAGE_SIZE = 50


class EvaluationStopped(Exception):
    """Only sanitized, locally authored messages are exposed."""


def load_cases():
    data = json.loads(CASES.read_text())
    assert len(data["seed"]) == 30
    assert len(data["duplicates"]) == len(data["changes"]) == 3
    assert len(data["queries"]) == 16
    ids = [item["id"] for key in ("seed", "duplicates", "changes", "queries") for item in data[key]]
    assert len(ids) == len(set(ids))
    return data


def plan(data):
    return {
        "mode": "plan_only", "network_requests": 0,
        "seed_facts": len(data["seed"]), "duplicate_submissions": len(data["duplicates"]),
        "changed_fact_submissions": len(data["changes"]), "searches": len(data["queries"]),
        "maximum_requests_including_status_and_lists": MAX_REQUESTS,
        "maximum_adds": 36, "maximum_searches": 16,
        "top_k": 5, "inference": "provider default, matching the application",
        "maximum_status_reads_per_write_attempt": MAX_POLLS,
        "owner": "new whisper-stack-eval-<UTC>-<random> owner for each run",
        "additional_spend_usd": None,
        "cost_note": "Request caps are not dollar caps. Verify plan, allowance, BYOK and overage settings first.",
        "not_measured": ["spoken answers", "answer abstention", "long-session context", "hardware", "large-store scaling"],
    }


def normalized(text):
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def matches(text, anchors):
    # Each anchor group needs one acceptable spelling in the SAME record.
    value = " " + normalized(text) + " "
    return all(any(" " + normalized(alias) + " " in value for alias in group) for group in anchors)


def projected_result(case, rows):
    ranks = [i + 1 for i, row in enumerate(rows) if matches(row["memory"], case["anchors"])] if case["anchors"] else []
    texts = [row["memory"] for row in rows]
    projection = json.dumps({"memories": texts}) if texts else "No memories found."
    return {
        "case_id": case["id"], "kind": case["kind"], "query": case["query"],
        "expected": case["expected"], "results": rows,
        "expected_anchor_rank": min(ranks) if ranks else None,
        "anchor_hit_at_5": bool(ranks) if case["anchors"] else None,
        "old_value_present": any(matches(text, [[case["old_value"]]]) for text in texts) if case.get("old_value") else None,
        "unknown_query_nonempty": bool(rows) if case["kind"] == "unknown" else None,
        "returned_characters": sum(len(text) for text in texts),
        "tool_result_utf8_bytes": len(projection.encode()),
        "review_required": True,
    }


class Runner:
    def __init__(self, client, owner, report_path, *, evidence="live_provider", sleep=time.sleep):
        if not re.fullmatch(r"whisper-stack-eval-\d{8}T\d{6}Z-[a-f0-9]{12}", owner):
            raise EvaluationStopped("An isolated generated evaluation owner is required.")
        self.client, self.owner, self.path, self.sleep = client, owner, report_path, sleep
        self.report = {
            "evidence": evidence, "owner": owner, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cases_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
            "requests": [], "writes": [], "snapshots": {}, "queries": [],
            "cost_usd": None, "provider_token_usage": None,
            "limits": {"requests": MAX_REQUESTS, "adds": 36, "searches": 16},
            "protocol": {"max_status_reads_per_wait": MAX_POLLS, "status_interval_seconds": 3, "phase_settle_seconds": 5},
        }
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report, indent=2) + "\n")
        temporary.replace(self.path)

    def request(self, operation, method, path, **kwargs):
        records = self.report["requests"]
        if len(records) >= MAX_REQUESTS:
            raise EvaluationStopped("Total request cap reached; no further requests made.")
        limit = {"add": 36, "search": 16}.get(operation)
        if limit is not None and sum(row["operation"] == operation for row in records) >= limit:
            raise EvaluationStopped("Operation cap reached; no further requests made.")
        row = {"operation": operation, "outcome": "attempting"}
        records.append(row)
        self.save()  # Persist intent before sending; an interrupted add is never replayed.
        started = time.perf_counter()
        try:
            with self.client.stream(method, path, **kwargs) as response:
                row["http_status"] = response.status_code
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise EvaluationStopped("Provider response exceeded the evaluation size limit.")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise EvaluationStopped("Unexpected provider response shape.")
                # Retain numeric usage only when actually reported; never fabricate cost.
                usage = data.get("usage")
                if isinstance(usage, dict):
                    row["provider_usage"] = {k: v for k, v in usage.items() if type(v) in (int, float)}
            row["outcome"] = "received"
            return data
        except EvaluationStopped:
            row["outcome"] = "stopped"
            raise
        except Exception:
            row["outcome"] = "failed"
            raise EvaluationStopped("Provider request failed; no retry made. See operation and HTTP status in report.") from None
        finally:
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.save()

    def rows(self, data, *, maximum=None):
        rows = data.get("results")
        if not isinstance(rows, list) or (maximum is not None and len(rows) > maximum):
            raise EvaluationStopped("Unexpected memory results shape or result limit violation.")
        safe = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("memory"), str):
                raise EvaluationStopped("Malformed memory record; not scored as empty.")
            if "user_id" in row and row["user_id"] != self.owner:
                raise EvaluationStopped("Provider returned a different owner's record; evaluation stopped.")
            safe.append({key: row[key] for key in ("id", "memory", "score", "created_at", "updated_at") if key in row})
        return safe

    def snapshot(self, name):
        rows = []
        expected_count = None
        for page in range(1, MAX_PAGES + 1):
            data = self.request("list", "POST", "/v3/memories/",
                                json={"filters": {"user_id": self.owner}},
                                params={"page": page, "page_size": PAGE_SIZE})
            count, next_page = data.get("count"), data.get("next")
            if type(count) is not int or count < 0 or (next_page is not None and not isinstance(next_page, str)):
                raise EvaluationStopped("Malformed memory page; not scored as empty.")
            if expected_count is not None and expected_count != count:
                raise EvaluationStopped("Memory count changed while paging; snapshot is inconclusive.")
            expected_count = count
            rows.extend(self.rows(data, maximum=PAGE_SIZE))
            if not next_page:
                break
        else:
            raise EvaluationStopped("Snapshot page cap reached; incomplete store is not scored.")
        ids = [row.get("id") for row in rows]
        if len(rows) != expected_count or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
            raise EvaluationStopped("Incomplete or overlapping snapshot; not scored.")
        self.report["snapshots"][name] = {"count": expected_count, "results": rows}
        self.save()
        return rows

    def wait_for_write(self, record):
        event = record.get("event_id")
        for _ in range(MAX_POLLS):
            if record["status"] == "SUCCEEDED":
                return
            if record["status"] not in {"PENDING", "RUNNING", "PROCESSING"} or not isinstance(event, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", event):
                raise EvaluationStopped("Write processing did not succeed; no retry made.")
            self.sleep(3)
            data = self.request("event", "GET", f"/v1/event/{quote(event, safe='')}/")
            record["status"] = data.get("status")
            self.save()
        if record["status"] != "SUCCEEDED":
            raise EvaluationStopped("Write processing incomplete after bounded status checks; no searches scored.")

    def write(self, case):
        started = time.perf_counter()
        record = {"case_id": case["id"], "message": case["text"], "status": "attempting"}
        self.report["writes"].append(record)
        self.save()
        data = self.request("add", "POST", "/v3/memories/add/",
                            json={"messages": [{"role": "user", "content": case["text"]}], "user_id": self.owner})
        record["status"] = data.get("status")
        event = data.get("event_id")
        if isinstance(event, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", event):
            record["event_id"] = event
        elif record["status"] != "SUCCEEDED":
            raise EvaluationStopped("Pending write has no valid event ID; not treated as saved.")
        self.wait_for_write(record)
        record["completion_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.save()

    def continue_from(self, previous, data):
        # Explicit continuation only, after an acknowledged pending job; never
        # replay a write whose transport outcome is unknown or redo searches.
        ordered = [case for phase in ("seed", "duplicates", "changes") for case in data[phase]]
        writes = previous.get("writes", [])
        valid = (
            previous.get("owner") == self.owner and previous.get("evidence") == self.report["evidence"]
            and previous.get("cases_sha256") == self.report["cases_sha256"]
            and previous.get("status") == "incomplete" and not previous.get("queries")
            and previous.get("stop_reason") == "Write processing incomplete after bounded status checks; no searches scored."
            and 0 < len(writes) <= len(ordered)
            and [row.get("case_id") for row in writes] == [row["id"] for row in ordered[:len(writes)]]
            and all(row.get("status") == "SUCCEEDED" for row in writes[:-1])
            and previous.get("requests", [{}])[-1].get("outcome") == "received"
        )
        if not valid:
            raise EvaluationStopped("Report is not eligible for continuation; uncertain writes are never replayed.")
        self.report = previous
        self.report.setdefault("continuations", []).append({
            "at": datetime.now(timezone.utc).isoformat(), "initial_stop": previous["stop_reason"],
            "previous_finished_at": previous.get("finished_at"), "max_status_reads_per_wait": MAX_POLLS,
            "note": "Explicit continuation; cumulative request/add/search caps and original owner preserved. No writes replayed.",
        })
        self.report.pop("stop_reason", None)
        self.report["status"] = "continuing"
        self.save()
        try:
            self.wait_for_write(writes[-1])
        except (EvaluationStopped, KeyboardInterrupt) as error:
            self.report["status"] = "incomplete"
            self.report["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else "Interrupted during explicit status continuation."
            self.save()
            raise
        writes[-1]["confirmation_delayed"] = True
        # Completion latency was not observed continuously: do not invent it.
        self.save()

    def run(self, data, *, continuing=False):
        try:
            if not continuing and self.snapshot("before"):
                raise EvaluationStopped("Evaluation owner is not empty; no writes made.")
            completed = {row["case_id"] for row in self.report["writes"] if row.get("status") == "SUCCEEDED"}
            for phase in ("seed", "duplicates", "changes"):
                if phase in self.report["snapshots"]:
                    continue
                for case in data[phase]:
                    if case["id"] not in completed:
                        self.write(case)
                # Fixed settling window; do not search repeatedly until a desired answer appears.
                self.sleep(5)
                self.snapshot(phase)
                print(f"{phase}: {len(data[phase])} completed submissions; snapshot saved", flush=True)
            for case in data["queries"]:
                response = self.request("search", "POST", "/v3/memories/search/",
                                        json={"query": case["query"], "filters": {"user_id": self.owner}, "top_k": 5})
                result = projected_result(case, self.rows(response, maximum=5))
                result["latency_ms"] = self.report["requests"][-1]["latency_ms"]
                self.report["queries"].append(result)
                self.save()
            self.summarize(data)
            self.report["status"] = "completed_pending_semantic_review"
        except (EvaluationStopped, KeyboardInterrupt) as error:
            self.report["status"] = "incomplete"
            self.report["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else "Interrupted; do not replay this owner's writes."
            raise
        finally:
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()

    def summarize(self, data):
        queries = self.report["queries"]
        known = [row for row in queries if row["anchor_hit_at_5"] is not None]
        snapshots = self.report["snapshots"]
        seed_rows = snapshots["seed"]["results"]
        final_rows = snapshots["changes"]["results"]
        counts = Counter(normalized(row["memory"]) for row in final_rows)
        self.report["summary"] = {
            "known_query_anchor_hits": sum(row["anchor_hit_at_5"] for row in known),
            "known_queries": len(known),
            "unknown_queries_with_results": sum(row["unknown_query_nonempty"] is True for row in queries),
            "unknown_queries": sum(row["kind"] == "unknown" for row in queries),
            "seed_facts_with_anchor_in_seed_snapshot": sum(any(matches(row["memory"], case["anchors"]) for row in seed_rows) for case in data["seed"]),
            "stored_counts_by_phase": {key: value["count"] for key, value in snapshots.items()},
            "identical_normalized_duplicate_records": sum(value - 1 for value in counts.values()),
            "search_latency_median_ms": statistics.median(row["latency_ms"] for row in queries),
            "search_latency_max_ms": max(row["latency_ms"] for row in queries),
            "max_tool_result_utf8_bytes": max(row["tool_result_utf8_bytes"] for row in queries),
            "requests_by_operation": dict(Counter(row["operation"] for row in self.report["requests"])),
            "interpretation": "Lexical diagnostics only; inspect semantic duplicates, temporal wording and answerability manually. No answer-generation score.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Submit fabricated facts to a NEW isolated hosted owner")
    parser.add_argument("--allowance-confirmed", action="store_true", help="Account allowance/BYOK/overages reviewed for this run; not a billing control")
    parser.add_argument("--continue-report", type=Path, help="Explicitly continue a pending-job report without replaying writes; keeps cumulative caps")
    args = parser.parse_args()
    data = load_cases()
    if args.continue_report and not args.live:
        parser.error("Continuation requires --live and a current allowance review.")
    if not args.live:
        print(json.dumps(plan(data), indent=2))
        return
    if not args.allowance_confirmed:
        parser.error("Review account allowance and billing before --live; --allowance-confirmed records that review, not a dollar cap.")
    # Only the live branch reads a key; do not read the personal owner or other providers' credentials.
    local = dotenv_values(HERE / ".env")
    key = os.environ.get("MEM0_API_KEY") or local.get("MEM0_API_KEY")
    if not key:
        parser.error("Missing MEM0_API_KEY")
    previous = None
    if args.continue_report:
        source = args.continue_report.resolve()
        if not source.is_relative_to((HERE / "output").resolve()):
            parser.error("Continuation reports must be inside the ignored evaluation output folder.")
        previous = json.loads(source.read_text())
        owner, folder = previous["owner"], source.parent
        path = folder / ("continued-" + uuid.uuid4().hex[:8] + ".json")
    else:
        owner = "whisper-stack-eval-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
        folder = HERE / "output" / owner
        folder.mkdir(parents=True, exist_ok=False)
        path = folder / "report.json"
    print(f"Live evaluation owner: {owner}\nEvidence: {path}", flush=True)
    try:
        with httpx.Client(base_url="https://api.mem0.ai", timeout=20, follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0),
                          headers={"Authorization": "Token " + key, "Accept": "application/json"}) as client:
            runner = Runner(client, owner, path)
            if previous:
                runner.continue_from(previous, data)
            runner.run(data, continuing=previous is not None)
        print(json.dumps(runner.report["summary"], indent=2))
    except (EvaluationStopped, KeyboardInterrupt):
        raise SystemExit("Evaluation incomplete; inspect the local report. No retry, cleanup or existing-owner changes were made.") from None


if __name__ == "__main__":
    main()
