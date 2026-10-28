"""Eight cold, text-only hosted-agent questions. Default execution is offline."""

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

from evaluate_memory import CASES, HERE, EvaluationStopped, load_cases
from run import configuration, register_memory_tools

CASE_IDS = {"query-drink", "query-city", "query-meeting", "query-language",
            "query-unknown-dog", "query-unknown-employer", "query-unknown-allergy", "query-unknown-code"}
MAX_HTTP = 64
MAX_LOOKUPS = 16
CASE_SECONDS = 50


def selected_cases():
    cases = [row for row in load_cases()["queries"] if row["id"] in CASE_IDS]
    assert len(cases) == 8
    return cases


def numeric_tree(value):
    if type(value) in (int, float):
        return value
    if isinstance(value, dict):
        return {key: result for key, item in value.items() if (result := numeric_tree(item)) is not None}
    return None


class ToolHandlers:
    def __init__(self):
        self.handlers = {}

    def register(self, name, handler, is_async=False):
        if not is_async:
            raise EvaluationStopped("Expected the shared asynchronous memory handlers.")
        self.handlers[name] = handler


class ReadOnlyMemory:
    def __init__(self, runner, case):
        self.runner, self.case = runner, case

    async def add(self, **kwargs):
        raise EvaluationStopped("Memory writes are blocked in the answer evaluation.")

    async def search(self, *, query, filters, top_k, latest_only):
        if filters != {"user_id": self.runner.owner} or top_k != 5 or latest_only is not True:
            raise EvaluationStopped("Shared recall departed from the isolated active-only contract.")
        if self.runner.lookups >= MAX_LOOKUPS or self.case["lookup_count"] >= 2:
            raise EvaluationStopped("Answer evaluation recall cap reached.")
        self.runner.lookups += 1
        self.case["lookup_count"] += 1
        response = await self.runner.request("mem0", "POST", "/v3/memories/search/", json={
            "query": query, "filters": filters, "top_k": top_k, "latest_only": latest_only,
        })
        rows = response.get("results")
        if not isinstance(rows, list) or len(rows) > 5 or any(
            not isinstance(row, dict) or not isinstance(row.get("memory"), str)
            or ("user_id" in row and row["user_id"] != self.runner.owner) for row in rows
        ):
            raise EvaluationStopped("Malformed or cross-owner memory results.")
        self.case["retrievals"].append({"query": query, "results": [
            {key: row[key] for key in ("id", "memory", "score") if key in row} for row in rows
        ]})
        self.runner.save()
        return response


class AnswerRunner:
    def __init__(self, clients, baseline, path, agent_id):
        self.clients, self.owner, self.path, self.agent_id = clients, baseline["owner"], path, agent_id
        if not re.fullmatch(r"whisper-stack-eval-\d{8}T\d{6}Z-[a-f0-9]{12}", self.owner):
            raise EvaluationStopped("Only a generated synthetic baseline owner is permitted.")
        self.lookups = 0
        self.report = {"status": "preparing", "evidence": "live_hosted_agent_text_only",
                       "started_at": datetime.now(timezone.utc).isoformat(), "memory_owner": self.owner,
                       "agent_id": agent_id, "requests": [], "cases": [], "credit_checks": [],
                       "limits": {"sessions": 8, "questions": 8, "mem0_searches": MAX_LOOKUPS,
                                  "http_requests": MAX_HTTP, "seconds_per_case": CASE_SECONDS},
                       "memory_writes": 0, "microphone_access": False, "cost_usd": None}
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report, indent=2) + "\n")
        temporary.replace(self.path)

    async def request(self, provider, method, path, **kwargs):
        allowed = (provider == "mem0" and method == "POST" and path == "/v3/memories/search/") or (
            provider == "elevenlabs" and method == "GET" and (
                path in {"/v1/user/subscription", f"/v1/convai/agents/{self.agent_id}", "/v1/convai/conversation/get-signed-url"}
                or re.fullmatch(r"/v1/convai/conversations/conv_[a-zA-Z0-9]+", path)
            ))
        if not allowed or len(self.report["requests"]) >= MAX_HTTP:
            raise EvaluationStopped("HTTP operation disallowed or evaluation request cap reached.")
        row = {"provider": provider, "operation": "search" if provider == "mem0" else "read", "outcome": "attempting"}
        self.report["requests"].append(row)
        self.save()
        started = time.perf_counter()
        try:
            async with self.clients[provider].stream(method, path, **kwargs) as response:
                row["http_status"] = response.status_code
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise EvaluationStopped("Evaluation HTTP response size limit exceeded.")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise EvaluationStopped("Unexpected HTTP response shape.")
                row["outcome"] = "received"
                return data
        except Exception:
            row["outcome"] = "failed"
            raise EvaluationStopped("Provider request failed; see HTTP status in the local report. No retry.") from None
        finally:
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.save()

    async def credits(self):
        data = await self.request("elevenlabs", "GET", "/v1/user/subscription")
        used, limit = data.get("character_count"), data.get("character_limit")
        if type(used) is not int or type(limit) is not int or limit - used < 10000 or data.get("status") != "active":
            raise EvaluationStopped("Included credit coverage could not be established.")
        if data.get("can_extend_character_limit") is not False or data.get("allowed_to_extend_character_limit") is not False:
            raise EvaluationStopped("Credit-limit extension is not confirmed disabled.")
        self.report["credit_checks"].append({"used": used, "limit": limit})
        self.save()
        if used - self.report["credit_checks"][0]["used"] >= 1000:
            raise EvaluationStopped("Observed account credit delta reached the evaluation stop threshold.")

    async def agent_snapshot(self):
        data = await self.request("elevenlabs", "GET", f"/v1/convai/agents/{self.agent_id}")
        config, platform = data["conversation_config"], data["platform_settings"]
        prompt = config["agent"]["prompt"]
        local = json.loads((HERE / "agent.json").read_text())["conversation_config"]["agent"]
        reference = json.loads((HERE / "agent-reference.json").read_text())
        if (prompt["llm"] != "claude-haiku-5-5" or prompt["max_tokens"] != 4096
                or prompt.get("backup_llm_config", {}).get("preference") != "disabled"
                or prompt["prompt"] != local["prompt"]["prompt"]
                or set(prompt["tool_ids"]) != set(reference["tool_ids"])
                or not platform["auth"]["enable_auth"]
                or platform["overrides"]["conversation_config_override"]["conversation"]["text_only"] is not True):
            raise EvaluationStopped("Existing agent/model/tool/text-only configuration differs from the accepted setup.")
        stable = {"conversation_config": config, "auth": platform["auth"], "overrides": platform["overrides"]}
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        return config["agent"]["first_message"], digest

    async def one_case(self, fixture, greeting):
        case = {"case_id": fixture["id"], "kind": fixture["kind"], "question": fixture["query"],
                "expected": fixture["expected"], "status": "starting", "lookup_count": 0,
                "retrievals": [], "tools": [], "responses": [], "event_types": [], "answer": None}
        self.report["cases"].append(case)
        self.save()
        handlers = ToolHandlers()
        register_memory_tools(handlers, ReadOnlyMemory(self, case), self.owner)
        started = time.perf_counter()
        async with asyncio.timeout(CASE_SECONDS):
            signed = await self.request("elevenlabs", "GET", "/v1/convai/conversation/get-signed-url", params={"agent_id": self.agent_id})
            address = signed.get("signed_url", "")
            parts = urlsplit(address)
            if parts.scheme != "wss" or parts.netloc != "api.elevenlabs.io" or parts.path != "/v1/convai/conversation":
                raise EvaluationStopped("Unexpected signed WebSocket destination.")
            case["session_user_id"] = "whisper-stack-answer-eval-" + uuid.uuid4().hex
            async with connect(address, open_timeout=10, close_timeout=3, max_size=128 * 1024, ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "conversation_initiation_client_data", "user_id": case["session_user_id"],
                                          "conversation_config_override": {"conversation": {"text_only": True}}}))
                sent, quiet_until, frames = False, None, 0
                while True:
                    wait = max(0.001, quiet_until - time.monotonic()) if quiet_until else 15
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=wait)
                    except asyncio.TimeoutError:
                        if case["answer"] and quiet_until:
                            break
                        raise EvaluationStopped("Agent did not return a complete answer within the event wait.") from None
                    frames += 1
                    if frames > 250 or not isinstance(raw, str):
                        raise EvaluationStopped("Unexpected or excessive session frames.")
                    message = json.loads(raw)
                    kind = message.get("type")
                    case["event_types"].append(kind)
                    if kind == "audio":
                        raise EvaluationStopped("Unexpected audio event in the text-only evaluation.")
                    if kind == "ping":
                        await ws.send(json.dumps({"type": "pong", "event_id": message["ping_event"]["event_id"]}))
                    elif kind == "conversation_initiation_metadata":
                        case["conversation_id"] = message["conversation_initiation_metadata_event"]["conversation_id"]
                        if sent:
                            raise EvaluationStopped("Duplicate session initiation.")
                        await ws.send(json.dumps({"type": "user_message", "text": fixture["query"]}))
                        sent = True
                    elif kind == "client_tool_call":
                        call = message["client_tool_call"]
                        name, parameters = call.get("tool_name"), call.get("parameters", {})
                        if name != "retrieveMemories" or len(case["tools"]) >= 2:
                            case["tools"].append({"name": name, "blocked": True})
                            raise EvaluationStopped("Non-retrieval or excessive tool call blocked; no memory write made.")
                        if not sent:
                            raise EvaluationStopped("Tool called before the test question.")
                        quiet_until = None
                        record = {"name": name, "parameters": parameters}
                        case["tools"].append(record)
                        self.save()
                        result = await handlers.handlers[name](parameters)
                        record["result"] = result
                        await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": call["tool_call_id"],
                                                  "result": result, "is_error": False}))
                    elif kind == "agent_response":
                        response = message["agent_response_event"]["agent_response"].strip()
                        if len(response) > 8000:
                            raise EvaluationStopped("Answer text exceeded the evaluation limit.")
                        case["responses"].append(response)
                        if response != greeting and sent:
                            case["answer"] = response
                            quiet_until = time.monotonic() + 2
                    elif kind in {"error", "internal_error"}:
                        raise EvaluationStopped("Hosted agent reported an error; raw details withheld.")
                    self.save()
        case["status"] = "answered_pending_review"
        case["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.save()

    async def run(self):
        try:
            await self.credits()
            greeting, before = await self.agent_snapshot()
            self.report["agent_config_sha256_before"] = before
            self.report["status"] = "running"
            for fixture in selected_cases():
                if len(self.report["cases"]) >= 8:
                    raise EvaluationStopped("Session cap reached.")
                await self.one_case(fixture, greeting)
                await self.credits()
                print(f"{fixture['id']}: answer captured", flush=True)
            await asyncio.sleep(5)
            for case in self.report["cases"]:
                identifier = case.get("conversation_id", "")
                if not re.fullmatch(r"conv_[a-zA-Z0-9]+", identifier):
                    raise EvaluationStopped("Unexpected conversation ID.")
                data = await self.request("elevenlabs", "GET", f"/v1/convai/conversations/{identifier}")
                metadata = data.get("metadata", {})
                case["provider_status"] = data.get("status")
                case["provider_text_only"] = data.get("conversation_initiation_client_data", {}).get("conversation_config_override", {}).get("conversation", {}).get("text_only")
                case["provider_numeric_metadata"] = numeric_tree({key: metadata.get(key) for key in ("cost", "charging", "llm_usage", "call_duration_secs")})
                case["provider_messages"] = [{"role": row.get("role"), "message": row.get("message")} for row in data.get("transcript", [])]
                self.save()
            _, after = await self.agent_snapshot()
            self.report["agent_config_sha256_after"] = after
            if before != after:
                raise EvaluationStopped("Agent configuration changed during the evaluation; inspect before interpreting results.")
            self.report["status"] = "completed_pending_semantic_review"
        except BaseException as error:
            self.report["status"] = "incomplete"
            self.report["stop_reason"] = str(error) if isinstance(error, EvaluationStopped) else type(error).__name__
            raise
        finally:
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save()


async def live(args):
    baseline = json.loads(args.baseline_report.read_text())
    if baseline.get("status") != "reviewed_retrieval_baseline" or baseline.get("cases_sha256") != hashlib.sha256(CASES.read_bytes()).hexdigest():
        raise EvaluationStopped("Use the reviewed synthetic baseline with unchanged fixtures.")
    values, reference = configuration()
    folder = HERE / "output" / "answer-evaluation"
    folder.mkdir(exist_ok=True)
    path = folder / ("answers-" + uuid.uuid4().hex[:8] + ".json")
    print(f"Evidence: {path}", flush=True)
    async with httpx.AsyncClient(base_url="https://api.elevenlabs.io", timeout=20, follow_redirects=False,
                                 transport=httpx.AsyncHTTPTransport(retries=0), headers={"xi-api-key": values["ELEVENLABS_API_KEY"]}) as eleven:
        async with httpx.AsyncClient(base_url="https://api.mem0.ai", timeout=15, follow_redirects=False,
                                     transport=httpx.AsyncHTTPTransport(retries=0), headers={"Authorization": "Token " + values["MEM0_API_KEY"]}) as memory:
            runner = AnswerRunner({"elevenlabs": eleven, "mem0": memory}, baseline, path, reference["agent_id"])
            await runner.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allowance-confirmed", action="store_true")
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"mode": "plan_only", "sessions": 8, "questions": [row["id"] for row in selected_cases()],
                          "max_mem0_searches": MAX_LOOKUPS, "memory_writes": 0, "audio": False,
                          "prompt_changes": False, "network_requests": 0}, indent=2))
        return
    if not args.allowance_confirmed or not args.baseline_report or not args.baseline_report.resolve().is_relative_to((HERE / "output").resolve()):
        parser.error("Live evaluation requires current included allowance checks and a local baseline report under output/.")
    try:
        asyncio.run(live(args))
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        raise SystemExit("Answer evaluation stopped; inspect local evidence. No retries, memory writes or agent changes were made.") from None


if __name__ == "__main__":
    main()
