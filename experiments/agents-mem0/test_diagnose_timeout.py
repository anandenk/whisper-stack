"""Offline diagnostic-plan and failure-evidence checks."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from diagnose_timeout import main, one_probe, probes
from evaluate_context import ContextRunner
from test_evaluate_answers import FakeSocket, OWNER


class TimeoutDiagnosisTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_default_never_loads_keys_or_opens_clients(self):
        with patch("sys.argv", ["diagnose_timeout.py"]), patch("diagnose_timeout.configuration", side_effect=AssertionError), patch("diagnose_timeout.httpx.AsyncClient", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as out:
            main()
        plan = json.loads(out.getvalue())
        self.assertEqual((plan["sessions"], plan["user_messages"], plan["network_requests"], plan["mem0_requests"]), (2, 4, 0, 0))
        self.assertEqual(len(plan["probes"][0]["questions"]), 3)
        self.assertEqual(len(plan["probes"][1]["questions"]), 1)

    async def test_failed_generation_still_collects_provider_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = ContextRunner({}, {"owner": OWNER}, Path(folder) / "report.json", "test-agent")
            runner.report.update(probes=[], turns=[], blocked_tool_count=0)
            runner.request = AsyncMock(side_effect=[
                {"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?secret=not-saved"},
                {"status": "failed", "transcript": [], "metadata": {"error": {"reason": "TimeoutError"}}},
            ])
            ws = FakeSocket([{"type": "conversation_initiation_metadata", "conversation_initiation_metadata_event": {"conversation_id": "conv_test"}}])
            with patch("diagnose_timeout.connect", return_value=ws), patch("diagnose_timeout.asyncio.sleep", new=AsyncMock()), contextlib.redirect_stdout(io.StringIO()):
                await one_probe(runner, probes()[1], "Hello")
            row = runner.report["probes"][0]
            self.assertEqual((row["status"], row["provider"]["status"]), ("stopped", "failed"))
            self.assertEqual(row["provider"]["metadata"]["error"]["reason"], "TimeoutError")
            self.assertEqual([m["type"] for m in ws.sent], ["conversation_initiation_client_data", "user_message"])
            self.assertNotIn("not-saved", runner.path.read_text())
            self.assertEqual(runner.request.await_count, 2)


if __name__ == "__main__":
    unittest.main()
