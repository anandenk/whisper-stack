"""Two isolated text probes to separate follow-up handling from question content."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit
import uuid

import httpx
from websockets.asyncio.client import connect

from evaluate_context import ContextRunner, EvaluationStopped, HERE, scenario
from run import configuration


def probes():
    return [
        {"id": "simple-followups", "questions": [
            {"id": "ready", "message": "Do not use memory tools during this conversation. Reply only Ready."},
            {"id": "next", "message": "Reply only Next."},
            {"id": "arithmetic", "message": "What is two plus two? Answer with just the number."},
        ]},
        {"id": "problem-question-first", "questions": [scenario()["intervening"][0]]},
    ]


async def one_probe(runner, fixture, greeting):
    row = {"id": fixture["id"], "status": "starting", "turns": []}
    runner.report["probes"].append(row)
    # next_answer writes only to this probe's turn list; each session is fresh.
    runner.report["turns"] = row["turns"]
    runner.save()
    signed = await runner.request("elevenlabs", "GET", "/v1/convai/conversation/get-signed-url", params={"agent_id": runner.agent_id})
    address = signed.get("signed_url", "")
    parts = urlsplit(address)
    if parts.scheme != "wss" or parts.netloc != "api.elevenlabs.io" or parts.path != "/v1/convai/conversation":
        raise EvaluationStopped("Unexpected signed WebSocket destination.")
    try:
        async with asyncio.timeout(120):
            async with connect(address, open_timeout=10, close_timeout=3, max_size=128 * 1024, ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "conversation_initiation_client_data", "user_id": "whisper-stack-diagnosis-" + uuid.uuid4().hex,
                                          "conversation_config_override": {"conversation": {"text_only": True}}}))
                async with asyncio.timeout(15):
                    while "conversation_id" not in row:
                        message = json.loads(await ws.recv())
                        if message.get("type") == "conversation_initiation_metadata":
                            row["conversation_id"] = message["conversation_initiation_metadata_event"]["conversation_id"]
                        elif message.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong", "event_id": message["ping_event"]["event_id"]}))
                        else:
                            raise EvaluationStopped("Unexpected pre-initiation event.")
                for question in fixture["questions"]:
                    await runner.next_answer(ws, question, greeting)
        row["status"] = "answered_pending_provider_check"
    except Exception as error:
        row["status"] = "stopped"
        row["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else type(error).__name__
    finally:
        runner.save()
    identifier = row.get("conversation_id")
    if identifier:
        await asyncio.sleep(5)
        for attempt in range(3):
            info = await runner.request("elevenlabs", "GET", f"/v1/convai/conversations/{identifier}")
            if info.get("status") in {"done", "failed"}:
                break
            if attempt < 2:
                await asyncio.sleep(5)
        # Synthetic text and provider diagnostics only; signed URLs are never saved.
        row["provider"] = {key: info.get(key) for key in ("status", "transcript", "metadata", "conversation_initiation_client_data")}
        runner.save()
    print(f"{fixture['id']}: {row['status']}; provider={row.get('provider', {}).get('status')}", flush=True)


async def live():
    values, reference = configuration()
    folder = HERE / "output" / "timeout-diagnosis"
    folder.mkdir(exist_ok=True)
    path = folder / ("probes-" + uuid.uuid4().hex[:8] + ".json")
    owner = "whisper-stack-eval-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    print(f"Diagnostic evidence: {path}", flush=True)
    async with httpx.AsyncClient(base_url="https://api.elevenlabs.io", timeout=20, follow_redirects=False,
                                 transport=httpx.AsyncHTTPTransport(retries=0), headers={"xi-api-key": values["ELEVENLABS_API_KEY"]}) as client:
        runner = ContextRunner({"elevenlabs": client}, {"owner": owner}, path, reference["agent_id"])
        runner.report.pop("memory_owner", None)
        runner.report.update(evidence="live_hosted_timeout_diagnosis", probes=[], turns=[], blocked_tool_count=0,
                             mem0_requests=0, limits={"sessions": 2, "user_messages": 4, "seconds_per_session": 120,
                                                    "http_requests": 64, "observed_credit_delta_stop": 500})
        try:
            await runner.credits()
            greeting, before = await runner.agent_snapshot()
            runner.report["agent_config_sha256_before"] = before
            for fixture in probes():
                if runner.report["credit_checks"][-1]["used"] - runner.report["credit_checks"][0]["used"] >= 500:
                    raise EvaluationStopped("Diagnostic credit-delta stop threshold reached.")
                await one_probe(runner, fixture, greeting)
                await runner.credits()
            _, after = await runner.agent_snapshot()
            runner.report["agent_config_sha256_after"] = after
            if before != after:
                raise EvaluationStopped("Agent settings changed during diagnosis.")
            runner.report["status"] = "completed_pending_review"
        finally:
            runner.report.pop("turns", None)
            runner.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            runner.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allowance-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"network_requests": 0, "sessions": 2, "user_messages": 4, "mem0_requests": 0, "probes": probes()}, indent=2))
        return
    if not args.allowance_confirmed:
        parser.error("Live diagnosis requires included-credit coverage.")
    try:
        asyncio.run(live())
    except Exception:
        raise SystemExit("Diagnostic run stopped; inspect local report. No automatic retry or setting change.") from None


if __name__ == "__main__":
    main()
