# Isolated ElevenLabs Agents + Mem0 test

Preferred client: **ElevenLabs’ official browser WebRTC SDK** (D24).
Open **http://127.0.0.1:8766** after starting the local page server. Edge audio
is user-accepted. The user also confirmed Chrome works after disabling their
privacy extensions; the specific extension/rule has not been isolated.

## Start the browser tester

From the repository root, using the already-installed environment:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py
```

Open the URL, click **Start conversation**, and allow microphone access.
**End conversation** stops the session. Page load does not request a provider
session or access the microphone. Start uses service credits and activates the
microphone/speaker. If End is pressed while SDK setup or permission is pending,
the eventual session is closed as soon as SDK setup returns.

The official `@elevenlabs/client` 1.31.0 handles capture/playback over WebRTC;
its LiveKit capture defaults enable echo cancellation, noise suppression, and
automatic gain control. No custom echo processing or microphone muting during
playback is implemented. The SDK's unmodified rawAudioProcessor worklet is served
locally. Configuration does not guarantee speaker echo performance: test it here.

The small loopback bridge only obtains the private-agent session token and
executes the two existing Mem0 client tools. Both provider keys stay server-side.
The SDK uses standard WebRTC audio rather than the earlier Python PCM path.
The working `backend/`, its running tester, and existing remote agent settings
are unchanged by D24. No cloud deployment or backend migration.

## Existing agent and memory configuration

- Agent `agent_placeholder_000000000000000000`, named
  **Whisper Stack - D23 Haiku 5.5 + Mem0 test**.
- Native `claude-haiku-5-5`, low effort, 1024-token cap, alternate LLM fallback
  disabled, concise answers. Authentication required.
- George and `eleven_flash_v2`. The English Agents API rejected Flash v2.5;
  the working backend retains its approved Flash v2.5 default.
- Tools: `addMemories` and `retrieveMemories`, both waiting for results.
  `agent-reference.json` holds their existing IDs. Do not recreate on resume.
- Hosted Mem0; stable local USER_ID `whisper-stack-d23-test` fixes both write and
  search scope. Tool arguments cannot select another user. SDK telemetry disabled.
- Mem0 V3 saves may be PENDING; that means submitted, not confirmed available.
  The runner does not retry or poll automatically.
- Local `.env` holds MEM0_API_KEY and USER_ID. Only ELEVENLABS_API_KEY is reused
  from `backend/.env` when not supplied locally. No Anthropic key is needed.
- No web-search parity or additional tools are included.

Mem0 authentication, remote agent/tool definitions, and mocked local contracts
are verified. The user accepted WebRTC speech recognition and speaker behavior in Edge.
The user also confirmed that a preference saved through Mem0 was recalled in
a new session. **Basic setup and collaborative evaluation are complete.**
Independent acoustic benchmarks and broader memory reliability remain unverified.
The user confirmed the older Python microphone fix worked, but its speaker
feedback prompted the move to the official WebRTC client.

ElevenLabs defaults record voice and retain conversations indefinitely
(retention_days=-1); built-in user memory is disabled. Hosted Mem0 persists
submitted facts. These are recorded defaults, not a settled long-term retention
policy. This page keeps transcript display in memory and writes no recordings
or conversation text to local files. Provider retention is separate.

## Repeating the accepted test

1. Ask a short question and let the reply finish. Check that its own output does
   not appear as a new **You** transcript or restart the conversation.
2. Ask for a longer reply, then speak over it to check natural interruption.
3. If audio works, ask it to remember a harmless test preference. Look for the
   memory result, End, allow processing, then Start a new session and ask recall.

Do not run live tests automatically. The dashboard tester alone does not run
these local Mem0 handlers; use this page for the integrated test.

## Reproduce and verify

The environment is installed on this Mac. For a fresh checkout:

```sh
brew install portaudio
python3 -m venv experiments/agents-mem0/.venv
experiments/agents-mem0/.venv/bin/python -m pip install -r experiments/agents-mem0/requirements.lock.txt
npm --prefix experiments/agents-mem0 ci
npm --prefix experiments/agents-mem0 run build
```

PortAudio/PyAudio support the preserved older Python tester; WebRTC runs in the
browser. Add keys to local `.env` using `.env.example` without overwriting an
existing credential file. Python pins and npm lockfile are independent of the
working backend. `web/dist/`, `.env`, environments, dependencies and `output/`
are ignored. `agent.json` preserves the current selected remote configuration
(including quiet waiting); `tools.json` preserves the tool definitions. The
tool IDs in `agent-reference.json` were attached to the agent at creation. These
IDs refer to existing resources in the user's account; checkout does not create
or provision another agent automatically.

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/run.py --verify
experiments/agents-mem0/.venv/bin/python -m unittest discover -s experiments/agents-mem0 -p 'test_*.py' -v
npm --prefix experiments/agents-mem0 test
```

`--verify` performs GETs for Mem0 ping and ElevenLabs agent/tool configuration
only. The 7 Python and 8 browser checks use mocks/synthetic audio; no real
microphone or provider generation. Browser checks also load the real SDK bundle
and official worklet under the page CSP. The test server uses fake credentials
and mocked upstreams. Local readback/evidence is in ignored `output/`.

The earlier `run.py --start` Python client, `audio.py` microphone-thread fix, and
regression checks remain as reference. They are not used by the WebRTC page.
There are no application-level provider retries; transport recovery behavior
inside the official WebRTC SDK remains at its supported defaults.

Sources: [ElevenLabs JavaScript SDK](https://elevenlabs.io/docs/eleven-agents/libraries/java-script),
[WebRTC token API](https://elevenlabs.io/docs/eleven-agents/api-reference/conversations/get-webrtc-token),
[official client source](https://github.com/elevenlabs/packages/blob/main/packages/client/src/utils/WebRTCConnection.ts),
[Mem0 integration](https://docs.mem0.ai/integrations/elevenlabs).

## Startup diagnostics

If startup fails, the page displays whether it failed obtaining a session token
or establishing microphone/WebRTC, followed by a sanitized SDK reason. Session
tokens and join-request data are redacted.

Chrome initially failed with “could not establish signal connection: Failed to
fetch.” Token authentication passed, and Edge worked. The user then disabled
multiple Chrome privacy extensions (including Privacy Badger among those
disabled) and reported that Chrome works too. This indicates extension
interference; the specific extension/rule is unknown. It does not establish
that Privacy Badger alone was responsible.

The official `webRtc: {singlePeerConnection: false}` option remains configured.
It was tried based on a [vendor signalling issue](https://github.com/elevenlabs/packages/issues/694),
but did not resolve Chrome's failure on its own; its necessity remains unverified.
No dependency downgrade, SDK patch, microphone gate or fallback transport.

## Quiet waiting after answers (D25)

The native agent setting `conversation_config.turn.turn_timeout=-1` disables
silence-triggered re-engagement (previously 7 seconds). Authenticated PATCH/GET
readback verified that only this setting changed. End/Start a fresh session to
load it, then verify the agent stays quiet after answering until you speak again.
The session remains open for follow-ups, subject to its unchanged 600-second
maximum duration. This is not auto-disconnect or a wake-word implementation.
The user's subsequent transcript contains no unsolicited check-ins, and they
reported successful interruptions and clean input. No automated paid conversation
was run. The user subsequently confirmed saved-preference recall in a fresh
session, completing the agreed basic memory test.
