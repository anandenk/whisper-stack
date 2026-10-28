"""Offline evaluation safety/accounting checks; mocks do not establish recall quality."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from evaluate_memory import EvaluationStopped, MAX_POLLS, Runner, load_cases, main, projected_result

OWNER = "whisper-stack-eval-20261027T180000Z-012345abcdef"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "report.json"
        self.calls = []

    def runner(self, handler):
        def record(request):
            self.calls.append(request)
            return handler(request)
        client = httpx.Client(base_url="https://api.mem0.ai", transport=httpx.MockTransport(record))
        self.addCleanup(client.close)
        return Runner(client, OWNER, self.path, evidence="mocked", sleep=lambda _: None)

    def test_default_plan_never_reads_credentials_or_opens_network(self):
        with patch("sys.argv", ["evaluate_memory.py"]), patch("evaluate_memory.dotenv_values", side_effect=AssertionError), patch("evaluate_memory.httpx.Client", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        plan = json.loads(output.getvalue())
        self.assertEqual((plan["network_requests"], plan["maximum_adds"], plan["maximum_searches"]), (0, 36, 16))

    def test_live_requires_allowance_review_before_reading_a_key(self):
        with patch("sys.argv", ["evaluate_memory.py", "--live"]), patch("evaluate_memory.dotenv_values", side_effect=AssertionError), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main()

    def test_rejects_existing_application_owner(self):
        with self.assertRaises(EvaluationStopped):
            Runner(None, "whisper-stack-d23-test", self.path)
        self.assertFalse(self.path.exists())

    def test_complete_mocked_run_preserves_contract_and_bounds(self):
        cases = load_cases()
        stored, pending = [], {}
        def provider(request):
            body = json.loads(request.content) if request.content else None
            route = request.url.path
            if route == "/v3/memories/":
                self.assertEqual(body, {"filters": {"user_id": OWNER}})
                self.assertEqual(request.url.params["page_size"], "50")
                return httpx.Response(200, json={"count": len(stored), "next": None, "results": stored})
            if route == "/v3/memories/add/":
                self.assertEqual(set(body), {"messages", "user_id"})
                self.assertEqual(body["user_id"], OWNER)
                event = f"event-{len(pending)}"
                pending[event] = body["messages"][0]["content"]
                return httpx.Response(200, json={"status": "PENDING", "event_id": event})
            if route.startswith("/v1/event/"):
                event = route.split("/")[-2]
                stored.append({"id": event, "memory": pending[event], "user_id": OWNER, "metadata": {"do_not_store": "private"}})
                return httpx.Response(200, json={"status": "SUCCEEDED", "payload": {"do_not_store": "private"}})
            self.assertEqual(route, "/v3/memories/search/")
            self.assertEqual(body["filters"], {"user_id": OWNER})
            self.assertEqual(body["top_k"], 5)
            self.assertEqual(set(body), {"query", "filters", "top_k"})
            return httpx.Response(200, json={"results": [{"id": "x", "memory": "unrelated synthetic fact", "user_id": OWNER, "score": 0.2}]})
        runner = self.runner(provider)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(cases)
        report = json.loads(self.path.read_text())
        self.assertEqual(report["status"], "completed_pending_semantic_review")
        self.assertEqual(report["summary"]["requests_by_operation"], {"list": 4, "add": 36, "event": 36, "search": 16})
        self.assertEqual(report["summary"]["known_query_anchor_hits"], 0)
        self.assertEqual(report["summary"]["unknown_queries_with_results"], 4)
        self.assertIsNone(report["cost_usd"])
        self.assertNotIn("do_not_store", self.path.read_text())

    def test_nonempty_owner_stops_before_any_write(self):
        runner = self.runner(lambda _: httpx.Response(200, json={"count": 1, "next": None, "results": [{"id": "x", "memory": "existing"}]}))
        with self.assertRaises(EvaluationStopped):
            runner.run(load_cases())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(runner.report["writes"], [])
        self.assertEqual(runner.report["status"], "incomplete")

    def test_pending_is_not_saved_and_does_not_trigger_search(self):
        def provider(request):
            if request.url.path == "/v3/memories/add/":
                return httpx.Response(200, json={"status": "PENDING", "event_id": "job"})
            return httpx.Response(200, json={"status": "RUNNING"})
        runner = self.runner(provider)
        with self.assertRaises(EvaluationStopped):
            runner.write(load_cases()["seed"][0])
        self.assertEqual(len(self.calls), MAX_POLLS + 1)
        self.assertEqual(runner.report["writes"][0]["status"], "RUNNING")
        self.assertEqual(runner.report["queries"], [])

    def test_http_failure_is_not_retried_or_exposed(self):
        runner = self.runner(lambda _: httpx.Response(429, json={"detail": "secret-provider-payload"}))
        with self.assertRaises(EvaluationStopped) as error:
            runner.write(load_cases()["seed"][0])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("secret-provider-payload", str(error.exception) + self.path.read_text())
        self.assertEqual(runner.report["requests"][0]["http_status"], 429)

    def test_add_attempt_cap_stops_before_sending(self):
        runner = self.runner(lambda _: httpx.Response(200, json={}))
        runner.report["requests"] = [{"operation": "add"}] * 36
        with self.assertRaises(EvaluationStopped):
            runner.request("add", "POST", "/v3/memories/add/", json={})
        self.assertEqual(self.calls, [])

    def test_incomplete_or_malformed_pages_are_not_empty_success(self):
        for response in [{}, {"count": 1, "next": None, "results": []}, {"count": 0, "next": {"bad": "url"}, "results": []}]:
            runner = self.runner(lambda _, response=response: httpx.Response(200, json=response))
            with self.assertRaises(EvaluationStopped):
                runner.snapshot("seed")
            self.assertNotIn("seed", runner.report["snapshots"])

    def test_cross_owner_or_excess_results_are_rejected(self):
        runner = self.runner(lambda _: httpx.Response(200, json={}))
        with self.assertRaises(EvaluationStopped):
            runner.rows({"results": [{"memory": "other person's fact", "user_id": "other"}]})
        with self.assertRaises(EvaluationStopped):
            runner.rows({"results": [{"memory": "x"}] * 6}, maximum=5)

    def test_change_and_unknown_scores_are_diagnostics_not_semantic_passes(self):
        cases = load_cases()
        result = projected_result(cases["queries"][0], [{"memory": "Prefers rooibos, has a chamomile plant."}])
        self.assertTrue(result["anchor_hit_at_5"])
        self.assertTrue(result["old_value_present"])
        self.assertTrue(result["review_required"])
        unknown = projected_result(cases["queries"][-1], [{"memory": "Likes jazz."}])
        self.assertIsNone(unknown["anchor_hit_at_5"])
        self.assertTrue(unknown["unknown_query_nonempty"])

    def test_explicit_pending_continuation_never_replays_completed_or_pending_add(self):
        cases = load_cases()
        stored = []
        def provider(request):
            body = json.loads(request.content) if request.content else None
            if request.url.path.startswith("/v1/event/"):
                return httpx.Response(200, json={"status": "SUCCEEDED"})
            if request.url.path == "/v3/memories/":
                return httpx.Response(200, json={"count": len(stored), "next": None, "results": stored})
            if request.url.path == "/v3/memories/add/":
                stored.append({"id": str(len(stored)), "memory": body["messages"][0]["content"]})
                return httpx.Response(200, json={"status": "SUCCEEDED"})
            return httpx.Response(200, json={"results": []})
        runner = self.runner(provider)
        previous = json.loads(json.dumps(runner.report))
        previous.update(status="incomplete", stop_reason="Write processing incomplete after bounded status checks; no searches scored.")
        previous["snapshots"]["before"] = {"count": 0, "results": []}
        previous["writes"] = [{"case_id": cases["seed"][0]["id"], "status": "SUCCEEDED"}, {"case_id": cases["seed"][1]["id"], "status": "RUNNING", "event_id": "pending"}]
        previous["requests"] = [{"operation": "add", "outcome": "received"}] * 2
        runner.continue_from(previous, cases)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(cases, continuing=True)
        adds = [json.loads(request.content) for request in self.calls if request.url.path == "/v3/memories/add/"]
        self.assertEqual(len(adds), 34)
        self.assertEqual(adds[0]["messages"][0]["content"], cases["seed"][2]["text"])
        self.assertEqual(runner.report["summary"]["requests_by_operation"]["add"], 36)
        self.assertTrue(runner.report["writes"][1]["confirmation_delayed"])

    def test_continuation_refuses_uncertain_transport_or_changed_fixtures(self):
        runner = self.runner(lambda _: httpx.Response(200, json={}))
        previous = json.loads(json.dumps(runner.report))
        previous.update(status="incomplete", stop_reason="Provider request failed")
        with self.assertRaises(EvaluationStopped):
            runner.continue_from(previous, load_cases())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
