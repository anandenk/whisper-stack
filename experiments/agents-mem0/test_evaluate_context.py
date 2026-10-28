"""Offline isolation checks for the single-conversation context evaluation."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluate_context import ContextRunner, DENIED, EvaluationStopped, main, scenario
from test_evaluate_answers import FakeSocket, OWNER


class ContextEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.runner = ContextRunner({}, {"owner": OWNER}, Path(self.folder.name) / "report.json", "test-agent")
        self.runner.report.update(turns=[], blocked_tool_count=0)

    async def test_offline_plan_never_loads_keys_or_opens_clients(self):
        with patch("sys.argv", ["evaluate_context.py"]), patch("evaluate_context.configuration", side_effect=AssertionError), patch("evaluate_context.httpx.AsyncClient", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as out:
            main()
        plan = json.loads(out.getvalue())
        self.assertEqual((plan["sessions"], plan["user_messages"], plan["mem0_requests"], plan["network_requests"]), (1, 22, 0, 0))
        data = scenario()
        for target in data["expected"].values():
            self.assertIn(target.casefold(), data["seed"]["message"].casefold())
            for turn in [*data["intervening"], data["recall"]]:
                self.assertNotIn(target.casefold(), turn["message"].casefold())

    async def test_http_boundary_blocks_all_mem0_and_mutation_requests(self):
        for provider, method, path in [("mem0", "POST", "/v3/memories/search/"), ("mem0", "GET", "/v1/memories/"), ("elevenlabs", "PATCH", "/v1/convai/agents/test-agent")]:
            with self.assertRaises(EvaluationStopped):
                await self.runner.request(provider, method, path)
        self.assertEqual(self.runner.report["requests"], [])

    async def test_memory_tools_are_denied_without_external_access_or_answer_injection(self):
        for name in ["addMemories", "retrieveMemories"]:
            ws = FakeSocket([
                {"type": "client_tool_call", "client_tool_call": {"tool_name": name, "tool_call_id": "blocked", "parameters": {"message": "private"}}},
                {"type": "agent_response", "agent_response_event": {"agent_response": "Understood."}},
            ])
            await self.runner.next_answer(ws, scenario()["seed"], "Hello")
            self.assertEqual(ws.sent[-1], {"type": "client_tool_result", "tool_call_id": "blocked", "result": DENIED, "is_error": True})
            for target in scenario()["expected"].values():
                self.assertNotIn(target.casefold(), DENIED.casefold())
        self.assertEqual(self.runner.report["blocked_tool_count"], 2)
        self.assertEqual(self.runner.report["requests"], [])

    async def test_multiple_turns_reuse_socket_and_send_only_fixture_questions(self):
        data = scenario()
        ws = FakeSocket([])
        turns = [data["intervening"][0], data["recall"]]
        for fixture in turns:
            ws.messages.append({"type": "agent_response", "agent_response_event": {"agent_response": "A synthetic answer."}})
            await self.runner.next_answer(ws, fixture, "Hello")
        self.assertEqual(ws.sent, [{"type": "user_message", "text": turn["message"]} for turn in turns])
        self.assertEqual(len(self.runner.report["turns"]), 2)

    async def test_audio_stops_before_any_external_action(self):
        ws = FakeSocket([{"type": "audio", "audio_event": {"audio_base_64": "not-retained"}}])
        with self.assertRaises(EvaluationStopped):
            await self.runner.next_answer(ws, scenario()["seed"], "Hello")
        self.assertEqual(self.runner.report["requests"], [])
        self.assertNotIn("not-retained", self.runner.path.read_text())


if __name__ == "__main__":
    unittest.main()
