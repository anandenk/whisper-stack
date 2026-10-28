"""Offline follow-up read-boundary checks; do not establish provider behavior."""

from datetime import datetime, timezone
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from evaluate_corrections import CorrectionRunner, main
from evaluate_memory import EvaluationStopped, load_cases

OWNER = "whisper-stack-eval-20261027T180000Z-012345abcdef"


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.calls = []

    def runner(self, provider=lambda _: httpx.Response(200, json={})):
        def record(request):
            self.calls.append(request)
            return provider(request)
        client = httpx.Client(base_url="https://api.mem0.ai", transport=httpx.MockTransport(record))
        self.addCleanup(client.close)
        return CorrectionRunner(client, OWNER, Path(self.folder.name) / "followup.json", evidence="mocked")

    def test_default_is_offline_and_does_not_read_credentials(self):
        with patch("sys.argv", ["evaluate_corrections.py"]), patch("evaluate_corrections.dotenv_values", side_effect=AssertionError), patch("evaluate_corrections.httpx.Client", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        result = json.loads(output.getvalue())
        self.assertEqual((result["network_requests"], result["writes"], result["searches"]), (0, 0, 9))

    def test_writes_settings_and_other_owner_are_rejected_before_transport(self):
        runner = self.runner()
        for operation, method, path, payload in [
            ("add", "POST", "/v3/memories/add/", {"user_id": OWNER}),
            ("detail", "DELETE", "/v1/memories/00000001-0000-0000-0000-000000000000/", {}),
            ("settings", "PATCH", "/project/", {}),
            ("search", "POST", "/v3/memories/search/", {"filters": {"user_id": "personal-owner"}}),
        ]:
            with self.assertRaises(EvaluationStopped):
                runner.request(operation, method, path, json=payload)
        self.assertEqual(self.calls, [])

    def test_lifecycle_survives_while_metadata_stays_out(self):
        runner = self.runner()
        row, = runner.rows({"results": [{"id": "old", "memory": "old fact", "user_id": OWNER,
                                        "lifecycle_state": "superseded", "replaced_by": "new",
                                        "metadata": {"private": "do not retain"}}]})
        self.assertEqual((row["lifecycle_state"], row["replaced_by"]), ("superseded", "new"))
        self.assertNotIn("metadata", row)

    def test_paired_followup_is_17_reads_with_unchanged_queries(self):
        cases = [case for case in load_cases()["queries"] if case["kind"] == "changed"]
        pairs, records = {}, {}
        for index, case in enumerate(cases):
            old, new = [f"{index * 2 + offset:08x}-0000-0000-0000-000000000000" for offset in (1, 2)]
            pairs[case["id"]] = {"old_id": old, "new_id": new}
            records[old] = {"id": old, "user_id": OWNER, "memory": "outdated fact", "lifecycle_state": "superseded", "replaced_by": new}
            records[new] = {"id": new, "user_id": OWNER, "memory": case["expected"], "lifecycle_state": "active", "replaced_by": None}
        def provider(request):
            if request.method == "GET":
                return httpx.Response(200, json=records[request.url.path.split("/")[-2]])
            body = json.loads(request.content)
            self.assertEqual(body["filters"], {"user_id": OWNER})
            rows = [row for row in records.values() if not body.get("latest_only") or row["lifecycle_state"] == "active"]
            if request.url.path == "/v3/memories/":
                return httpx.Response(200, json={"count": len(rows), "next": None, "results": rows})
            self.assertEqual(body["top_k"], 5)
            return httpx.Response(200, json={"results": rows[:5]})
        runner = self.runner(provider)
        runner.followup({"finished_at": datetime.now(timezone.utc).isoformat()}, cases, pairs)
        self.assertEqual(len(self.calls), 17)
        self.assertEqual(len(runner.report["queries"]), 9)
        self.assertEqual(runner.report["writes"], [])
        for first, second in zip(runner.report["queries"][0:6:2], runner.report["queries"][1:6:2]):
            self.assertEqual(first["query"], second["query"])
            self.assertEqual((first["mode"], second["mode"]), ("provider_default", "latest_only"))
        self.assertEqual(runner.report["status"], "completed_pending_semantic_review")

    def test_followup_stops_at_its_own_smaller_cap(self):
        runner = self.runner()
        runner.report["requests"] = [{"operation": "search"}] * 9
        with self.assertRaises(EvaluationStopped):
            runner.request("search", "POST", "/v3/memories/search/", json={"filters": {"user_id": OWNER}})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
