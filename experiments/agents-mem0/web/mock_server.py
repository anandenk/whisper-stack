"""Only for browser checks: fake credentials and mocked upstreams."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from web_server import create_app

memory = AsyncMock()
memory.add.return_value = {"status": "PENDING"}
memory.search.return_value = {"results": []}
memory.get_all.return_value = {"count": 0, "next": None, "previous": None, "results": []}
app = create_app(
    values={"ELEVENLABS_API_KEY": "fake-eleven", "MEM0_API_KEY": "fake-mem0", "USER_ID": "test-user"},
    reference={"agent_id": "test-agent"},
    provider_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"token": "fake-session-token"})),
    memory_factory=lambda: memory,
)
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), access_log=False)
