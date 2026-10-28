"""Loopback-only voice tester and memory viewer. No provider calls on page load."""

import asyncio
import argparse
from contextlib import asynccontextmanager
import json
import secrets

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from run import HERE, ConfigurationError, configuration, register_memory_tools

from elevenlabs.conversational_ai.conversation import ClientTools
from mem0 import AsyncMemoryClient


def create_app(values=None, reference=None, provider_transport=None, memory_factory=None):
    if values is None or reference is None:
        values, reference = configuration()
    local_token = secrets.token_urlsafe(32)
    memory = None
    tools = ClientTools()
    memory_lock = asyncio.Lock()

    async def memory_client():
        nonlocal memory
        async with memory_lock:
            if memory is None:
                if memory_factory:
                    memory = memory_factory()
                else:
                    # Construction validates the key; defer until an explicit memory action.
                    memory = await asyncio.to_thread(
                        AsyncMemoryClient, api_key=values["MEM0_API_KEY"],
                        client=httpx.AsyncClient(timeout=15, follow_redirects=False),
                    )
                register_memory_tools(tools, memory, values["USER_ID"])
        return memory

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

    @app.post("/api/memories")
    async def memories(request: Request, page: int = Query(default=1, ge=1, le=100_000)):
        # Clients choose only the page, never the owner, filters or provider URL.
        if set(request.query_params) - {"page"}:
            raise HTTPException(400, "Only a page number is supported")
        try:
            client = await memory_client()
            result = await client.get_all(
                filters={"user_id": values["USER_ID"]}, page=page, page_size=25,
            )
            items, count, next_page = result["results"], result["count"], result["next"]
            if (not isinstance(items, list) or len(items) > 25
                    or type(count) is not int or count < 0
                    or (next_page is not None and not isinstance(next_page, str))):
                raise ValueError("Invalid memory page")
            visible = []
            for item in items:
                text = item["memory"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Invalid memory text")
                # Return memory text only, not provider metadata or pagination URLs.
                visible.append({"memory": text})
        except Exception:
            raise HTTPException(502, "Could not load saved memories. No retry was made.") from None
        return {"memories": visible, "count": count, "page": page, "has_more": bool(next_page)}

    @app.post("/api/tools/{name}")
    async def tool(name: str, request: Request):
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
            await memory_client()
            result = await tools.handle(name, {"message": message})
        except Exception:
            raise HTTPException(502, "Mem0 tool failed; no retry was made.") from None
        return {"result": result}

    return app


def local_setup():
    """Validate local files/settings without constructing either provider client."""
    values, reference = configuration()
    for relative, instruction in [
        ("web/dist/client.js", "Run npm --prefix experiments/agents-mem0 run build first."),
        ("node_modules/@elevenlabs/client/worklets/rawAudioProcessor.js",
         "Run npm --prefix experiments/agents-mem0 ci first."),
    ]:
        if not (HERE / relative).is_file():
            raise ConfigurationError(instruction)
    return values, reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check local setup only; no server or provider calls")
    parser.add_argument("--port", type=int, default=8766, help="Loopback port (default: 8766)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        values, reference = local_setup()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    if args.check:
        print("Local settings, existing agent reference and browser assets are ready. "
              "No provider calls, server or microphone access. Provider access is not checked.")
        return

    import uvicorn
    print(f"Open http://127.0.0.1:{args.port} — Start conversation activates microphone and service-credit use.")
    uvicorn.run(create_app(values, reference), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
