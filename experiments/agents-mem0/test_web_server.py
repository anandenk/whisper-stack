"""Mocked loopback bridge checks; never contact providers."""

import unittest
from unittest.mock import AsyncMock

import httpx

from web_server import create_app


class WebServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider_requests = []

        def provider(request):
            self.provider_requests.append(request)
            return httpx.Response(200, json={"token": "temporary-test-token"})

        self.memory = AsyncMock()
        self.memory.add.return_value = {"status": "PENDING"}
        self.memory.search.return_value = {"results": [{"memory": "Likes teal"}]}
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

    async def test_private_token_uses_fixed_agent_and_keeps_key_server_side(self):
        response = await self.client.post("/api/session", headers=self.headers)
        self.assertEqual(response.json(), {"conversationToken": "temporary-test-token"})
        request, = self.provider_requests
        self.assertEqual(request.url.params["agent_id"], "isolated-agent")
        self.assertEqual(request.headers["xi-api-key"], "secret-eleven")

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
                                          json={"message": "favorite color"})
        self.assertIn("Likes teal", response.json()["result"])
        self.memory.search.assert_awaited_once_with(query="favorite color", filters={"user_id": "fixed-test-user"}, top_k=5)

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
