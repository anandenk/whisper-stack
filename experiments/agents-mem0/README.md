# Isolated ElevenLabs Agents + Mem0 test

Selected as the **main backend architecture** on 2026-10-27 (PROJECT.md D27).
The directory name and local implementation are unchanged; the original
experiment-only boundary below describes setup history. Deployment and device
integration remain unfinished. See [remaining milestones](../../docs/PROJECT.md#proposed-remaining-milestones-after-d27).

Preferred client: **ElevenLabs’ official browser WebRTC SDK** (D24).
The default port is **8766**; the existing saved tester is on **8767**.
Reuse **http://127.0.0.1:8767** while that tester is running. Edge audio
is user-accepted. The user also confirmed Chrome works after disabling their
privacy extensions; the specific extension/rule has not been isolated.

## Start the browser tester

From the repository root, using the already-installed environment:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py --check
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py
```

`--check` checks settings, the existing agent reference and browser assets locally;
it starts no server, microphone or provider request. Missing settings/files give
short instructions without credential values or a setup traceback. It does not
verify key validity, remote settings or whether a port is free. `--port 8767`
selects that port when starting a service later. If a tester is already running,
reuse it; a port-in-use error is not a reason to kill the existing process.

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

## View saved memories

On the tester page, scroll to **Saved memories** and choose **View saved memories**.
This reads memories for the same fixed owner used by conversation recall. It
does not start a conversation or enable the microphone. **Refresh** reloads the
first page; **Previous** and **Next** browse longer lists, 25 memories per page.
New saves may still be processing, so they may not appear immediately.

The list loads only when requested. A failed load is shown separately from an
empty list. Memory text stays in page memory; the bridge does not save or log it.
The list uses the existing same-origin/local-page-token checks and keeps the
owner filter server-side. Only text and paging counts reach the page, not provider
metadata. There are no editing/deletion controls in this increment (D28).
Mem0 documents this read operation as
[Get Memories](https://docs.mem0.ai/api-reference/memory/get-memories).

Normal conversation recall uses Mem0's native `latest_only=True` filter (D31)
to exclude superseded facts. This viewer retains the provider's default history
listing, including superseded records; no memories are deleted. The recall tool
does not currently offer a separate historical-query mode. D30's
[live comparison](evaluation/native-corrections-2026-10-27.md) verified the filter
on three corrections; generated-answer reliability is separate evaluation work.

## Existing agent and memory configuration

The separate [bounded memory evaluation](evaluation/README.md) uses fabricated
facts and a fresh owner to investigate extraction/retrieval without changing the
conversation tester. Its default command is offline; live runs consume Mem0
allowance and require a current account/budget check (D29).

- Configured ElevenLabs Conversational AI Agent (configured via
  `agent-reference.json`, template in `agent.json`).
- Native `claude-haiku-5-5`, low effort, 4,096-token thinking/answer cap,
  10-second model wait, alternate LLM fallback
  disabled, concise answers. Authentication required.
- George and `eleven_flash_v2`. The English Agents API rejected Flash v2.5;
  the working backend retains its approved Flash v2.5 default.
- Tools: `addMemories` and `retrieveMemories`, both waiting for results.
  `agent-reference.json` holds your configured agent and tool IDs (copy from
  `agent-reference.example.json`).
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

For the personal-prototype access boundary, saved-data controls and usage checks,
see [Personal prototype essentials](#personal-prototype-essentials). This page
keeps transcript display in memory and writes no recordings or conversation text
to local files. Provider retention is separate; evaluation runners deliberately
save synthetic transcripts/evidence in ignored `output/`.

## Personal prototype essentials

This is a private, single-owner tester. The server binds to `127.0.0.1`, checks
Host, and requires a matching Origin plus a page token for POSTs. These checks
block unrelated web pages; they are not user accounts or protection against other
software running on this computer. Do not expose the tester with a public tunnel
or bind it to a network interface. Device/remote access needs a separate design.

Keys remain server-side in ignored `.env` files (both existing files are mode
600 on this Mac). `.env.*` variants are also ignored except `.env.example`.
Do not put real keys in example files, transcripts or notes. Conversation tools
always use the configured owner; client arguments cannot select someone else.
Synthetic evaluation runners use separate generated owners. Normal recall hides
superseded facts; hiding a fact is not deletion. Spoken facts can be saved by the
existing agent: do not dictate passwords, API keys or access codes. Automated
secret detection in spoken content is not implemented.

Saved data has separate locations:

- **ElevenLabs:** conversations/transcripts and, when enabled, call audio.
  The historical setup recorded audio saving and `retention_days=-1`; current
  account findings are in [the handoff](../../docs/STATUS.md). The current public
  [retention documentation](https://elevenlabs.io/docs/eleven-agents/customization/privacy/retention)
  describes a two-year default, so defaults do not establish this agent's settings.
  The agent's Advanced settings contain retention and audio-saving controls.
  Turning off [audio saving](https://elevenlabs.io/docs/eleven-agents/customization/privacy/audio-saving)
  affects future recordings, not existing ones or transcript retention.
  The provider also supports deleting an
  [individual conversation](https://elevenlabs.io/docs/eleven-agents/api-reference/conversations/delete).
- **Mem0:** extracted facts submitted by the memory tool, including synthetic
  evaluation memories. Inspect the correct owner in the provider dashboard before
  removing anything. The documented [single-memory deletion operation](https://docs.mem0.ai/api-reference/memory/delete-memory)
  takes a memory ID; leave linked/bulk deletion out of a single-record removal.
  ElevenLabs conversation deletion and Mem0 memory deletion are separate actions.
  No Mem0 automatic expiry is configured by this app; account/backend retention
  and backup erasure are not established by this audit.
- **Local:** page transcript/list contents are in browser memory. Ignored
  `output/` contains persistent synthetic evaluation evidence; refreshing the page
  or deleting provider data does not remove those files.

No retention setting or data was changed in this audit. Provider deletion
operations are documented, not exercised. No custom delete interface is needed.

For usage, open ElevenLabs **Developers → Analytics → Usage**
([official instructions](https://elevenlabs.io/docs/help-center/account/general/how-can-i-see-information-about-credit-usage-for-my-account))
and the account's Subscription page for its allowance. Use Mem0's dashboard
Billing/usage view for add/retrieval allowance; its current account numbers were
not refreshed in this audit. Included credits, nominal USD/token estimates and
actual cash payments are different measures. No automatic account polling or
new diagnostics dashboard is added.

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
(including quiet waiting, the 4,096-token thinking/answer cap and 10-second
model-response wait); `tools.json` preserves the tool definitions. To configure
your private agent, copy `agent-reference.example.json` to `agent-reference.json`
and supply your own ElevenLabs agent ID and tool IDs created using `agent.json`
and `tools.json`. Checkout does not create or provision an agent automatically.

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/run.py --verify
experiments/agents-mem0/.venv/bin/python -m unittest discover -s experiments/agents-mem0 -p 'test_*.py' -v
npm --prefix experiments/agents-mem0 test
```

`--verify` performs GETs for Mem0 ping and ElevenLabs agent/tool configuration
only. The 49 Python and 10 browser checks last passed with mocks/synthetic
input; no real microphone or provider generation in those suites. Python now
includes evaluation-runner isolation checks; live evaluation evidence is
separate and linked from [the handoff](../../docs/STATUS.md). Browser checks also load the real SDK bundle
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

| Problem | First check |
| --- | --- |
| Server will not start | Run `web_server.py --check`; fix the named setting, reference or asset. |
| Port already in use | Reuse the existing tester; check the saved port in STATUS.md. |
| Session token fails | Check ElevenLabs key/access and account allowance; the page hides upstream details. |
| Microphone/WebRTC fails | Check browser microphone permission and privacy extensions; the page shows a redacted SDK reason. |
| Memory read/save fails | Check Mem0 key/access and allowance. Failure is not an empty list or a successful save; retry manually. |
| Usage unclear | Check provider usage pages above; the tester does not display an account balance. |

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
