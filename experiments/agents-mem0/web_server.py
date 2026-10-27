"""Loopback-only WebRTC test page. No provider calls until a user starts it."""

import asyncio
from contextlib import asynccontextmanager
import json
import secrets

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from run import HERE, configuration, register_memory_tools

from elevenlabs.conversational_ai.conversation import ClientTools
from mem0 import AsyncMemoryClient


def create_app(values=None, reference=None, provider_transport=None, memory_factory=None):
    if values is None or reference is None:
        values, reference = configuration()
    local_token = secrets.token_urlsafe(32)
    memory = None
    tools = ClientTools()
    memory_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        if memory is not None:
            await memory.async_client.aclose()
        tools.thread_pool.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request, call_next):
        host = request.headers.get("host", "")
        hostname = host.split(":")[0]
        if hostname not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "Loopback host required"}, status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin != "http://" + host or not secrets.compare_digest(
                request.headers.get("x-local-token", ""), local_token
            ):
                return JSONResponse({"detail": "Local page required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self' https://api.elevenlabs.io https://livekit.rtc.elevenlabs.io wss://livekit.rtc.elevenlabs.io; "
            "media-src 'self' blob:; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    async def index():
        return FileResponse(HERE / "web/index.html")

    @app.get("/client.js")
    async def script():
        return FileResponse(HERE / "web/dist/client.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet():
        return FileResponse(HERE / "web/style.css", media_type="text/css")

    @app.get("/raw-audio-processor.js")
    async def audio_worklet():
        # Serve the SDK's unmodified worklet locally so CSP need not allow blob scripts.
        return FileResponse(HERE / "node_modules/@elevenlabs/client/worklets/rawAudioProcessor.js",
                            media_type="text/javascript")

    @app.get("/api/config")
    async def config():
        return {"localToken": local_token, "userId": values["USER_ID"]}

    @app.post("/api/session")
    async def session():
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                         transport=provider_transport) as client:
                response = await client.get(
                    "https://api.elevenlabs.io/v1/convai/conversation/token",
                    params={"agent_id": reference["agent_id"]},
                    headers={"xi-api-key": values["ELEVENLABS_API_KEY"]},
                )
                response.raise_for_status()
                token = response.json()["token"]
                if not isinstance(token, str) or not token:
                    raise ValueError("Invalid token response")
        except Exception:
            raise HTTPException(502, "Could not obtain an ElevenLabs session token.") from None
        return {"conversationToken": token}

    @app.post("/api/tools/{name}")
    async def tool(name: str, request: Request):
        nonlocal memory
        if name not in {"addMemories", "retrieveMemories"}:
            raise HTTPException(404, "Unknown tool")
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 16_384:
                raise HTTPException(413, "Tool arguments too large")
        try:
            parameters = json.loads(body)
            message = parameters.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > 4000:
                raise ValueError()
        except (ValueError, AttributeError):
            raise HTTPException(400, "A message of 1–4000 characters is required") from None
        try:
            async with memory_lock:
                if memory is None:
                    if memory_factory:
                        memory = memory_factory()
                    else:
                        # SDK construction validates the key; defer it until a real tool call.
                        memory = await asyncio.to_thread(
                            AsyncMemoryClient, api_key=values["MEM0_API_KEY"],
                            client=httpx.AsyncClient(timeout=15, follow_redirects=False),
                        )
                    register_memory_tools(tools, memory, values["USER_ID"])
            result = await tools.handle(name, {"message": message})
        except Exception:
            raise HTTPException(502, "Mem0 tool failed; no retry was made.") from None
        return {"result": result}

    return app


if __name__ == "__main__":
    import uvicorn

    if not (HERE / "web/dist/client.js").exists():
        raise SystemExit("Run npm --prefix experiments/agents-mem0 run build first.")
    print("Open http://127.0.0.1:8766 — Start conversation activates microphone and paid service use.")
    uvicorn.run(create_app(), host="127.0.0.1", port=8766, access_log=False)
