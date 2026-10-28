"""Mocked loopback bridge checks; never contact providers."""

import unittest
from unittest.mock import AsyncMock, patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json

import httpx

from web_server import create_app, main


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.here = Path(self.folder.name)
        self.reference = self.here / "agent-reference.json"
        self.reference.write_text(json.dumps({"agent_id": "existing-agent", "tool_ids": ["save", "recall"]}))
        for relative in ["web/dist/client.js", "node_modules/@elevenlabs/client/worklets/rawAudioProcessor.js"]:
            target = self.here / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("// test asset")
        for target in ["run.HERE", "web_server.HERE"]:
            self.enterContext(patch(target, self.here))
        self.enterContext(patch.dict("os.environ", {}, clear=True))
        self.settings = {"ELEVENLABS_API_KEY": "private-eleven", "MEM0_API_KEY": "private-mem0", "USER_ID": "fixed-owner"}
        self.enterContext(patch("run.dotenv_values", return_value=self.settings))

    def test_offline_check_never_constructs_clients_or_starts_service(self):
        output = StringIO()
        with patch("sys.argv", ["web_server.py", "--check"]), redirect_stdout(output), \
                patch("web_server.create_app") as app, patch("uvicorn.run") as serve, \
                patch("web_server.AsyncMemoryClient") as memory, patch("web_server.httpx.AsyncClient") as provider:
            main()
        for operation in [app, serve, memory, provider]:
            operation.assert_not_called()
        self.assertIn("Provider access is not checked", output.getvalue())
        self.assertNotIn("private-", output.getvalue())

    def test_missing_settings_are_named_without_values_or_traceback(self):
        self.settings.pop("MEM0_API_KEY")
        with patch("sys.argv", ["web_server.py", "--check"]), self.assertRaises(SystemExit) as failure:
            main()
        self.assertEqual(str(failure.exception), "Missing local settings: MEM0_API_KEY")

    def test_invalid_reference_does_not_echo_its_contents(self):
        for contents in ["private-invalid-json", "[]", '{"agent_id": "private-value"}']:
            self.reference.write_text(contents)
            with self.subTest(contents=contents), patch("sys.argv", ["web_server.py", "--check"]), \
                    self.assertRaises(SystemExit) as failure:
                main()
            self.assertIn("agent-reference.json", str(failure.exception))
            self.assertNotIn("private-", str(failure.exception))

    def test_missing_assets_explain_the_build_step(self):
        (self.here / "web/dist/client.js").unlink()
        with patch("sys.argv", ["web_server.py", "--check"]), self.assertRaises(SystemExit) as failure:
            main()
        self.assertIn("npm --prefix experiments/agents-mem0 run build", str(failure.exception))


class WebServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider_requests = []

        def provider(request):
            self.provider_requests.append(request)
            return httpx.Response(200, json={"token": "temporary-test-token"})

        self.memory = AsyncMock()
        self.memory.add.return_value = {"status": "PENDING"}
        self.memory.search.return_value = {"results": [{"memory": "Likes teal"}]}
        self.memory.get_all.return_value = {"count": 0, "next": None, "results": []}
        self.app = create_app(
            values={"ELEVENLABS_API_KEY": "secret-eleven", "MEM0_API_KEY": "secret-mem0", "USER_ID": "fixed-test-user"},
            reference={"agent_id": "isolated-agent"},
            provider_transport=httpx.MockTransport(provider), memory_factory=lambda: self.memory,
        )
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8766")
        config = (await self.client.get("/api/config")).json()
        self.headers = {"Origin": "http://127.0.0.1:8766", "X-Local-Token": config["localToken"]}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def test_page_load_has_no_provider_calls_or_exposed_keys(self):
        for path in ["/", "/api/config", "/client.js", "/style.css", "/raw-audio-processor.js"]:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("secret-eleven", response.text)
            self.assertNotIn("secret-mem0", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.provider_requests, [])
        self.memory.add.assert_not_awaited()
        self.memory.search.assert_not_awaited()
        self.memory.get_all.assert_not_awaited()

    async def test_memory_list_is_owner_scoped_paginated_and_minimal(self):
        self.memory.get_all.return_value = {
            "count": 26, "next": "https://private-provider.example/page3?token=secret",
            "results": [{"id": "memory-id", "memory": "Likes teal", "lifecycle_state": "superseded",
                         "replaced_by": "new-memory-id", "metadata": {"private": "hidden"}}],
        }
        response = await self.client.post("/api/memories?page=2", headers=self.headers,
                                          json={"user_id": "other-user", "filters": {"user_id": "other-user"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "memories": [{"memory": "Likes teal"}], "count": 26, "page": 2, "has_more": True,
        })
        self.memory.get_all.assert_awaited_once_with(
            filters={"user_id": "fixed-test-user"}, page=2, page_size=25,
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.provider_requests, [])
        self.memory.add.assert_not_awaited()
        self.memory.search.assert_not_awaited()

    async def test_memory_list_requires_local_page_and_valid_page(self):
        for headers in [{}, {**self.headers, "Origin": "https://unrelated.example"}]:
            self.assertEqual((await self.client.post("/api/memories", headers=headers)).status_code, 403)
        for query in ["page=0", "page=-1", "page=abc", "page=100001"]:
            self.assertEqual((await self.client.post("/api/memories?" + query, headers=self.headers)).status_code, 422)
        self.assertEqual((await self.client.post("/api/memories?user_id=other", headers=self.headers)).status_code, 400)
        self.assertEqual((await self.client.get("/api/memories", headers=self.headers)).status_code, 405)
        self.memory.get_all.assert_not_awaited()

    async def test_empty_memories_are_distinct_from_provider_failure(self):
        response = await self.client.post("/api/memories", headers=self.headers)
        self.assertEqual(response.json(), {"memories": [], "count": 0, "page": 1, "has_more": False})
        self.memory.get_all.reset_mock()
        self.memory.get_all.side_effect = RuntimeError("private-key-and-provider-details")
        response = await self.client.post("/api/memories", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-key", response.text)
        self.memory.get_all.assert_awaited_once()

    async def test_malformed_memory_pages_are_not_reported_as_empty(self):
        for result in [
            {}, {"results": [], "count": "0", "next": None},
            {"results": [{"memory": None}], "count": 1, "next": None},
            {"results": [], "count": 0, "next": {"token": "private"}},
        ]:
            with self.subTest(result=result):
                self.memory.get_all.return_value = result
                response = await self.client.post("/api/memories", headers=self.headers)
                self.assertEqual(response.status_code, 502)

    async def test_private_token_uses_fixed_agent_and_keeps_key_server_side(self):
        response = await self.client.post("/api/session", headers=self.headers)
        self.assertEqual(response.json(), {"conversationToken": "temporary-test-token"})
        request, = self.provider_requests
        self.assertEqual(request.url.params["agent_id"], "isolated-agent")
        self.assertEqual(request.headers["xi-api-key"], "secret-eleven")

    async def test_session_failure_hides_provider_details_and_does_not_retry(self):
        def failing_provider(request):
            self.provider_requests.append(request)
            return httpx.Response(401, json={"detail": "private-provider-key-and-error"})

        with patch("web_server.httpx.AsyncClient", return_value=httpx.AsyncClient(
                transport=httpx.MockTransport(failing_provider))):
            response = await self.client.post("/api/session", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider", response.text)
        self.assertEqual(len(self.provider_requests), 1)

    async def test_cross_origin_and_missing_token_requests_are_rejected(self):
        for headers in [{}, {**self.headers, "Origin": "https://unrelated.example"}]:
            self.assertEqual((await self.client.post("/api/session", headers=headers)).status_code, 403)
        self.assertEqual(self.provider_requests, [])
        self.assertEqual((await self.client.get("/", headers={"Host": "unrelated.example"})).status_code, 403)

    async def test_memory_tools_keep_user_scope_and_pending_status(self):
        response = await self.client.post("/api/tools/addMemories", headers=self.headers,
                                          json={"message": "Likes teal", "user_id": "other-user"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("not yet confirmed", response.json()["result"])
        self.memory.add.assert_awaited_once_with(messages=[{"role": "user", "content": "Likes teal"}], user_id="fixed-test-user")
        response = await self.client.post("/api/tools/retrieveMemories", headers=self.headers,
                                          json={"message": "favorite color", "latest_only": False, "user_id": "other-user"})
        self.assertIn("Likes teal", response.json()["result"])
        self.memory.search.assert_awaited_once_with(
            query="favorite color", filters={"user_id": "fixed-test-user"}, top_k=5, latest_only=True,
        )

    async def test_invalid_or_failed_memory_call_does_not_claim_success(self):
        self.assertEqual((await self.client.post("/api/tools/other", headers=self.headers, json={})).status_code, 404)
        self.assertEqual((await self.client.post("/api/tools/addMemories", headers=self.headers, json={"message": ""})).status_code, 400)
        self.assertEqual((await self.client.post("/api/tools/addMemories", headers=self.headers, content=b"x" * 17000)).status_code, 413)
        self.memory.add.side_effect = RuntimeError("private-provider-detail")
        response = await self.client.post("/api/tools/addMemories", headers=self.headers, json={"message": "test"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-detail", response.text)


if __name__ == "__main__":
    unittest.main()
