"""Offline answer-runner isolation checks; no provider/model calls."""

import asyncio
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from evaluate_answers import AnswerRunner, EvaluationStopped, ReadOnlyMemory, main, selected_cases
from run import register_memory_tools
from evaluate_answers import ToolHandlers
from evaluate_memory import HERE

OWNER = "whisper-stack-eval-20261027T180000Z-012345abcdef"


class FakeSocket:
    def __init__(self, messages):
        self.messages, self.sent = list(messages), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        if not self.messages:
            raise asyncio.TimeoutError()
        return json.dumps(self.messages.pop(0))


class AnswerEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.runner = AnswerRunner({}, {"owner": OWNER}, Path(self.folder.name) / "report.json", "test-agent")

    async def test_offline_default_does_not_load_keys_or_start_clients(self):
        with patch("sys.argv", ["evaluate_answers.py"]), patch("evaluate_answers.configuration", side_effect=AssertionError), patch("evaluate_answers.httpx.AsyncClient", side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as out:
            main()
        result = json.loads(out.getvalue())
        self.assertEqual((result["sessions"], result["network_requests"], result["memory_writes"]), (8, 0, 0))

    async def test_write_and_other_owner_are_blocked(self):
        case = {"lookup_count": 0, "retrievals": []}
        memory = ReadOnlyMemory(self.runner, case)
        with self.assertRaises(EvaluationStopped):
            await memory.add(messages=[{"role": "user", "content": "fake"}], user_id=OWNER)
        with self.assertRaises(EvaluationStopped):
            await memory.search(query="fake", filters={"user_id": "personal"}, top_k=5, latest_only=True)
        self.assertEqual(self.runner.report["requests"], [])

    async def test_actual_shared_handler_uses_active_only_synthetic_owner(self):
        case = {"lookup_count": 0, "retrievals": []}
        self.runner.request = AsyncMock(return_value={"results": [{"id": "new", "memory": "Prefers chamomile."}]})
        handlers = ToolHandlers()
        register_memory_tools(handlers, ReadOnlyMemory(self.runner, case), OWNER)
        response = await handlers.handlers["retrieveMemories"]({"message": "drink"})
        self.assertEqual(json.loads(response), {"memories": ["Prefers chamomile."]})
        self.runner.request.assert_awaited_once_with("mem0", "POST", "/v3/memories/search/", json={
            "query": "drink", "filters": {"user_id": OWNER}, "top_k": 5, "latest_only": True,
        })

    async def test_http_boundary_rejects_configuration_and_memory_writes(self):
        for provider, method, path in [("mem0", "POST", "/v3/memories/add/"), ("elevenlabs", "PATCH", "/v1/convai/agents/test-agent")]:
            with self.assertRaises(EvaluationStopped):
                await self.runner.request(provider, method, path)
        self.assertEqual(self.runner.report["requests"], [])

    async def test_cold_session_sends_only_question_and_production_tool_result(self):
        fixture = selected_cases()[0]
        ws = FakeSocket([
            {"type": "conversation_initiation_metadata", "conversation_initiation_metadata_event": {"conversation_id": "conv_test"}},
            {"type": "agent_response", "agent_response_event": {"agent_response": "Hello"}},
            {"type": "client_tool_call", "client_tool_call": {"tool_name": "retrieveMemories", "tool_call_id": "tool1", "parameters": {"message": "drink"}}},
            {"type": "agent_response", "agent_response_event": {"agent_response": "You prefer chamomile tea."}},
        ])
        self.runner.request = AsyncMock(side_effect=[{"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?secret=not-logged"}, {"results": [{"memory": "Prefers chamomile tea."}]}])
        with patch("evaluate_answers.connect", return_value=ws):
            await self.runner.one_case(fixture, "Hello")
        init = ws.sent[0]
        self.assertEqual(init["conversation_config_override"], {"conversation": {"text_only": True}})
        self.assertNotEqual(init["user_id"], OWNER)
        self.assertEqual(ws.sent[1], {"type": "user_message", "text": fixture["query"]})
        self.assertEqual(ws.sent[2]["type"], "client_tool_result")
        self.assertEqual(self.runner.report["cases"][0]["answer"], "You prefer chamomile tea.")
        self.assertNotIn("not-logged", self.runner.path.read_text())

    async def test_audio_or_save_tool_stops_without_executing_it(self):
        for event in [
            {"type": "audio", "audio_event": {"audio_base_64": "do-not-retain"}},
            {"type": "client_tool_call", "client_tool_call": {"tool_name": "addMemories", "tool_call_id": "bad", "parameters": {"message": "do-not-save"}}},
        ]:
            ws = FakeSocket([event])
            self.runner.request = AsyncMock(return_value={"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation"})
            with patch("evaluate_answers.connect", return_value=ws), self.assertRaises(EvaluationStopped):
                await self.runner.one_case(selected_cases()[0], "Hello")
            self.runner.request.assert_awaited_once()
            self.assertEqual(len(ws.sent), 1)

    async def test_credit_extension_and_low_balance_prevent_generation(self):
        for subscription in [
            {"status": "active", "character_count": 900, "character_limit": 1000},
            {"status": "active", "character_count": 0, "character_limit": 100000, "can_extend_character_limit": True, "allowed_to_extend_character_limit": True},
        ]:
            self.runner.request = AsyncMock(return_value=subscription)
            with self.assertRaises(EvaluationStopped):
                await self.runner.credits()

    async def test_snapshot_accepts_selected_cap_and_rejects_old_or_unselected_caps(self):
        local = json.loads((HERE / "agent.json").read_text())
        reference = json.loads((HERE / "agent-reference.json").read_text())
        local["conversation_config"]["agent"]["prompt"]["tool_ids"] = reference["tool_ids"]
        local["platform_settings"]["overrides"] = {"conversation_config_override": {"conversation": {"text_only": True}}}
        self.runner.request = AsyncMock(return_value=local)
        greeting, digest = await self.runner.agent_snapshot()
        self.assertEqual(greeting, local["conversation_config"]["agent"]["first_message"])
        self.assertEqual(len(digest), 64)
        for cap in (1024, 8192):
            changed = copy.deepcopy(local)
            changed["conversation_config"]["agent"]["prompt"]["max_tokens"] = cap
            self.runner.request = AsyncMock(return_value=changed)
            with self.assertRaises(EvaluationStopped):
                await self.runner.agent_snapshot()


if __name__ == "__main__":
    unittest.main()
