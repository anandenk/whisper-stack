"""One bounded text-only conversation; external memory access is always denied."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

import httpx
from websockets.asyncio.client import connect

from evaluate_answers import AnswerRunner, numeric_tree
from evaluate_memory import EvaluationStopped, HERE
from run import configuration

FIXTURE = HERE / "evaluation" / "context-cases.json"
SESSION_SECONDS = 300
TURN_SECONDS = 40
DENIED = "External memory access is disabled for this isolated conversation; no memory was read or written."


def scenario():
    data = json.loads(FIXTURE.read_text())
    if len(data["intervening"]) != 20 or len(data["expected"]) != 3:
        raise EvaluationStopped("Unexpected context fixture size.")
    for turn in data["intervening"] + [data["recall"]]:
        if any(value.casefold() in turn["message"].casefold() for value in data["expected"].values()):
            raise EvaluationStopped("Recall targets must appear only in the seed user message.")
    return data


class ContextRunner(AnswerRunner):
    async def request(self, provider, method, path, **kwargs):
        if provider != "elevenlabs" or method != "GET":
            raise EvaluationStopped("Context evaluation permits only ElevenLabs read/auth requests; no Mem0 access.")
        data = await super().request(provider, method, path, **kwargs)
        if path == f"/v1/convai/agents/{self.agent_id}":
            conversation = data["conversation_config"]["conversation"]
            limit = conversation.get("max_duration_seconds")
            if type(limit) is not int or limit < SESSION_SECONDS:
                raise EvaluationStopped("Existing session limit is shorter than the bounded test.")
            self.report["provider_session_limit_seconds"] = limit
            self.report["compaction_config_readback"] = {
                key: value for key, value in (conversation.get("compaction") or {}).items()
                if value is None or type(value) in (bool, int, float)
            }
            self.save()
        return data

    async def next_answer(self, ws, fixture, greeting):
        turn = {"id": fixture["id"], "question": fixture["message"], "responses": [],
                "answer": None, "blocked_tools": [], "event_types": [], "status": "sending"}
        self.report["turns"].append(turn)
        self.save()
        started, quiet_until, frames = time.perf_counter(), None, 0
        async with asyncio.timeout(TURN_SECONDS):
            await ws.send(json.dumps({"type": "user_message", "text": fixture["message"]}))
            while True:
                wait = max(0.001, quiet_until - time.monotonic()) if quiet_until else 15
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=wait)
                except asyncio.TimeoutError:
                    if turn["answer"] and quiet_until:
                        break
                    raise EvaluationStopped("Context turn did not produce an answer within the event wait.") from None
                frames += 1
                if frames > 350 or not isinstance(raw, str):
                    raise EvaluationStopped("Unexpected or excessive context frames.")
                message = json.loads(raw)
                kind = message.get("type")
                turn["event_types"].append(kind)
                if kind == "audio":
                    raise EvaluationStopped("Unexpected audio in the text-only context test.")
                if kind == "ping":
                    await ws.send(json.dumps({"type": "pong", "event_id": message["ping_event"]["event_id"]}))
                elif kind == "client_tool_call":
                    call = message["client_tool_call"]
                    if call.get("tool_name") not in {"addMemories", "retrieveMemories"}:
                        raise EvaluationStopped("Unexpected non-memory tool; no action executed.")
                    if self.report["blocked_tool_count"] >= 8:
                        raise EvaluationStopped("Blocked-tool cap reached; no memory action executed.")
                    self.report["blocked_tool_count"] += 1
                    turn["blocked_tools"].append({"name": call["tool_name"], "result": DENIED})
                    quiet_until = None
                    await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": call["tool_call_id"],
                                              "result": DENIED, "is_error": True}))
                elif kind == "agent_response":
                    response = message["agent_response_event"]["agent_response"].strip()
                    if len(response) > 8000:
                        raise EvaluationStopped("Context answer size limit exceeded.")
                    turn["responses"].append(response)
                    if response != greeting:
                        turn["answer"] = response
                        quiet_until = time.monotonic() + 2
                elif kind == "context_usage":
                    turn.setdefault("provider_context_usage", []).append(numeric_tree(message))
                elif kind in {"error", "internal_error"}:
                    raise EvaluationStopped("Hosted agent reported a context-session error; details withheld.")
                self.save()
        turn["status"] = "answered_pending_review"
        turn["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.save()
        return turn

    async def run_context(self, data):
        self.report.pop("memory_owner", None)
        self.report.update(
            evidence="live_hosted_in_session_context", turns=[], blocked_tool_count=0,
            mem0_requests=0, fixture_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            limits={"sessions": 1, "user_messages": 22, "seconds": SESSION_SECONDS,
                    "seconds_per_turn": TURN_SECONDS, "memory_requests": 0, "http_requests": 64},
        )
        try:
            await self.credits()
            greeting, before = await self.agent_snapshot()
            self.report["agent_config_sha256_before"] = before
            signed = await self.request("elevenlabs", "GET", "/v1/convai/conversation/get-signed-url", params={"agent_id": self.agent_id})
            address = signed.get("signed_url", "")
            parts = urlsplit(address)
            if parts.scheme != "wss" or parts.netloc != "api.elevenlabs.io" or parts.path != "/v1/convai/conversation":
                raise EvaluationStopped("Unexpected signed WebSocket destination.")
            self.report["status"] = "running"
            self.report["session_user_id"] = "whisper-stack-context-eval-" + uuid.uuid4().hex
            started = time.perf_counter()
            async with asyncio.timeout(SESSION_SECONDS):
                async with connect(address, open_timeout=10, close_timeout=3, max_size=128 * 1024, ping_interval=None) as ws:
                    await ws.send(json.dumps({"type": "conversation_initiation_client_data", "user_id": self.report["session_user_id"],
                                              "conversation_config_override": {"conversation": {"text_only": True}}}))
                    async with asyncio.timeout(15):
                        while "conversation_id" not in self.report:
                            message = json.loads(await ws.recv())
                            if message.get("type") == "conversation_initiation_metadata":
                                self.report["conversation_id"] = message["conversation_initiation_metadata_event"]["conversation_id"]
                            elif message.get("type") == "ping":
                                await ws.send(json.dumps({"type": "pong", "event_id": message["ping_event"]["event_id"]}))
                            else:
                                raise EvaluationStopped("Unexpected pre-initiation event; context session stopped.")
                    turns = [data["seed"], *data["intervening"], data["recall"]]
                    for index, fixture in enumerate(turns):
                        await self.next_answer(ws, fixture, greeting)
                        if index and index % 5 == 0:
                            await self.credits()
                            print(f"Context: {index + 1}/{len(turns)} exchanges captured", flush=True)
            self.report["session_elapsed_seconds"] = round(time.perf_counter() - started, 3)
            await asyncio.sleep(5)
            identifier = self.report["conversation_id"]
            if not re.fullmatch(r"conv_[a-zA-Z0-9]+", identifier):
                raise EvaluationStopped("Unexpected context conversation ID.")
            for attempt in range(3):
                info = await self.request("elevenlabs", "GET", f"/v1/convai/conversations/{identifier}")
                if info.get("status") == "done":
                    break
                if attempt < 2:
                    await asyncio.sleep(5)
            self.report["provider_status"] = info.get("status")
            self.report["provider_text_only"] = info.get("conversation_initiation_client_data", {}).get("conversation_config_override", {}).get("conversation", {}).get("text_only")
            metadata = info.get("metadata", {})
            self.report["provider_numeric_metadata"] = numeric_tree({key: metadata.get(key) for key in ("cost", "charging", "llm_usage", "call_duration_secs")})
            self.report["provider_messages"] = [{"role": row.get("role"), "message": row.get("message")} for row in info.get("transcript", [])]
            if info.get("status") != "done" or self.report["provider_text_only"] is not True:
                raise EvaluationStopped("Provider did not confirm a completed text-only session; review required.")
            _, after = await self.agent_snapshot()
            self.report["agent_config_sha256_after"] = after
            if before != after:
                raise EvaluationStopped("Agent configuration changed during the context test.")
            await self.credits()
            self.report["status"] = "completed_pending_recall_review"
        except BaseException as error:
            self.report["status"] = "incomplete"
            self.report["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else type(error).__name__
            raise
        finally:
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()


async def live():
    data = scenario()
    values, reference = configuration()
    folder = HERE / "output" / "context-evaluation"
    folder.mkdir(exist_ok=True)
    path = folder / ("context-" + uuid.uuid4().hex[:8] + ".json")
    owner = "whisper-stack-eval-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    print(f"Context evidence: {path}", flush=True)
    async with httpx.AsyncClient(base_url="https://api.elevenlabs.io", timeout=20, follow_redirects=False,
                                 transport=httpx.AsyncHTTPTransport(retries=0), headers={"xi-api-key": values["ELEVENLABS_API_KEY"]}) as client:
        runner = ContextRunner({"elevenlabs": client}, {"owner": owner}, path, reference["agent_id"])
        await runner.run_context(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allowance-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.live:
        data = scenario()
        print(json.dumps({"mode": "plan_only", "network_requests": 0, "sessions": 1,
                          "user_messages": len(data["intervening"]) + 2, "intervening_exchanges": len(data["intervening"]),
                          "seconds_cap": SESSION_SECONDS, "mem0_requests": 0, "audio": False,
                          "script_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest()}, indent=2))
        return
    if not args.allowance_confirmed:
        parser.error("Live context evaluation requires a current included-credit review.")
    try:
        asyncio.run(live())
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        raise SystemExit("Context test stopped; inspect local evidence. No retry, Mem0 access or agent setting change.") from None


if __name__ == "__main__":
    main()
