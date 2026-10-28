"""Isolated D23 client tools; no conversation starts without --start."""

import argparse
import asyncio
import json
import os
from pathlib import Path

os.environ["MEM0_TELEMETRY"] = "false"

import httpx
from dotenv import dotenv_values
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import AsyncConversation, ClientTools
from mem0 import AsyncMemoryClient

HERE = Path(__file__).resolve().parent


class ConfigurationError(ValueError):
    """An actionable setup error containing no credential values."""


def configuration():
    local = dotenv_values(HERE / ".env")
    backend = dotenv_values(HERE.parents[1] / "backend" / ".env")
    # Reuse only the existing speech key; never change backend configuration.
    values = {k: os.environ.get(k) or local.get(k) for k in
              ("ELEVENLABS_API_KEY", "MEM0_API_KEY", "USER_ID")}
    values["ELEVENLABS_API_KEY"] = values["ELEVENLABS_API_KEY"] or backend.get("ELEVENLABS_API_KEY")
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise ConfigurationError("Missing local settings: " + ", ".join(missing))
    try:
        reference = json.loads((HERE / "agent-reference.json").read_text())
    except OSError:
        raise ConfigurationError("Cannot read agent-reference.json; restore the existing agent reference.") from None
    except (ValueError, UnicodeError):
        raise ConfigurationError("Invalid agent-reference.json; restore the existing agent reference.") from None
    if (not isinstance(reference, dict)
            or not isinstance(reference.get("agent_id"), str) or not reference["agent_id"].strip()
            or not isinstance(reference.get("tool_ids"), list) or len(reference["tool_ids"]) != 2
            or any(not isinstance(item, str) or not item.strip() for item in reference["tool_ids"])):
        raise ConfigurationError("agent-reference.json must identify the existing agent and two memory tools.")
    return values, reference


def register_memory_tools(tools, memory, user_id):
    def message(parameters):
        value = parameters.get("message")
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError("message must contain 1–4000 characters")
        return value.strip()

    async def add_memories(parameters):
        text = message(parameters)
        try:
            result = await memory.add(messages=[{"role": "user", "content": text}], user_id=user_id)
        except Exception:
            raise RuntimeError("Mem0 save failed; no retry was made.") from None
        status = result.get("status", "UNKNOWN").upper()
        print(f"[Mem0] addMemories: {status}")
        if status in {"PENDING", "RUNNING", "PROCESSING"}:
            return "Memory submitted for processing; it is not yet confirmed available for recall."
        if status == "SUCCEEDED" or result.get("results"):
            return "Memory saved."
        raise RuntimeError("Memory save was not confirmed.")

    async def retrieve_memories(parameters):
        text = message(parameters)
        try:
            result = await memory.search(
                query=text, filters={"user_id": user_id}, top_k=5, latest_only=True,
            )
            memories = [item["memory"] for item in result["results"]]
        except Exception:
            raise RuntimeError("Mem0 retrieval failed; no retry was made.") from None
        print(f"[Mem0] retrieveMemories: {len(memories)} result(s)")
        return json.dumps({"memories": memories}) if memories else "No memories found."

    tools.register("addMemories", add_memories, is_async=True)
    tools.register("retrieveMemories", retrieve_memories, is_async=True)


def verify(values, reference):
    """Read configuration/auth only: no search, memory write, or conversation."""
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get("https://api.mem0.ai/v1/ping/",
                              headers={"Authorization": "Token " + values["MEM0_API_KEY"]})
        response.raise_for_status()
        print("Mem0 authentication: verified (GET /v1/ping/)")
        headers = {"xi-api-key": values["ELEVENLABS_API_KEY"]}
        root = "https://api.elevenlabs.io/v1/convai"
        response = client.get(f"{root}/agents/{reference['agent_id']}", headers=headers)
        response.raise_for_status()
        agent = response.json()
        prompt = agent["conversation_config"]["agent"]["prompt"]
        if prompt["llm"] != "claude-haiku-5-5" or set(prompt["tool_ids"]) != set(reference["tool_ids"]):
            raise ValueError("Agent model/tool configuration differs from D23")
        if not agent["platform_settings"]["auth"]["enable_auth"]:
            raise ValueError("Agent authentication must be enabled")
        names = set()
        for tool_id in reference["tool_ids"]:
            response = client.get(f"{root}/tools/{tool_id}", headers=headers)
            response.raise_for_status()
            config = response.json()["tool_config"]
            if config["type"] != "client" or not config["expects_response"]:
                raise ValueError("Expected a client tool with a response")
            names.add(config["name"])
        if names != {"addMemories", "retrieveMemories"}:
            raise ValueError("Memory tool names differ from the runner")
    print("ElevenLabs agent and both client tool definitions: verified")


async def start(values, reference):
    from audio import MicrophoneAudioInterface

    # This path alone opens the microphone, plays sound, and starts paid service use.
    tools = ClientTools(loop=asyncio.get_running_loop())
    transport = httpx.AsyncClient(timeout=15, follow_redirects=False)
    async with AsyncMemoryClient(api_key=values["MEM0_API_KEY"], client=transport) as memory:
        register_memory_tools(tools, memory, values["USER_ID"])

        async def agent_response(text):
            print("Agent:", text)

        async def user_transcript(text):
            print("You:", text)

        ended = False

        async def session_ended():
            nonlocal ended
            ended = True

        audio = MicrophoneAudioInterface()
        conversation = AsyncConversation(
            ElevenLabs(api_key=values["ELEVENLABS_API_KEY"]), reference["agent_id"],
            user_id=values["USER_ID"], requires_auth=True, audio_interface=audio,
            client_tools=tools, callback_agent_response=agent_response,
            callback_user_transcript=user_transcript, callback_end_session=session_ended,
        )
        print("Starting live microphone/speaker test. Ctrl+C ends the session.")
        try:
            await conversation.start_session()
            await conversation.wait_for_session_end()
        finally:
            # The SDK audio stop method expects start() to have created its stream.
            if not ended and hasattr(audio, "out_stream"):
                await conversation.end_session()
            else:
                tools.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="Read-only provider authentication/configuration checks")
    mode.add_argument("--start", action="store_true", help="Start live microphone/speaker conversation (billable)")
    args = parser.parse_args()
    try:
        values, reference = configuration()
        if args.verify:
            verify(values, reference)
        elif args.start:
            asyncio.run(start(values, reference))
        else:
            print("Local settings present. No provider calls or microphone access. Use --verify for setup checks.")
    except KeyboardInterrupt:
        print("Session stopped.")
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    except ValueError:
        # Only ConfigurationError messages above are safe to expose.
        raise SystemExit("Setup failed; check local configuration and provider access.") from None
    except Exception as error:
        raise SystemExit(f"Setup/session failed ({type(error).__name__}); provider details withheld.") from None


if __name__ == "__main__":
    main()
