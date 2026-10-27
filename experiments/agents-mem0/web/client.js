import { Conversation } from "@elevenlabs/client";

const start = document.querySelector("#start");
const stop = document.querySelector("#stop");
const status = document.querySelector("#status");
const levels = document.querySelector("#levels");
const transcript = document.querySelector("#transcript");
let config, conversation, busy = false, cancelled = false, timer;

function safeError(error) {
  const name = error?.name || "Error";
  const message = error?.message || String(error || "Unknown error");
  return `${name}: ${message}`
    .replace(/([?&](?:access_token|token|conversation_token|join_request)=)[^&\s]+/gi, "$1[redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b/g, "[redacted token]")
    .slice(0, 600);
}

function log(who, message) {
  document.querySelector("#empty").hidden = true;
  const item = document.createElement("li");
  item.textContent = `${who}: ${message}`;
  transcript.append(item);
  while (transcript.children.length > 100) transcript.firstElementChild.remove();
}

async function post(path, body = {}) {
  const response = await fetch(path, {
    method: "POST", headers: {"Content-Type": "application/json", "X-Local-Token": config.localToken},
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail;
    throw new Error(`Local service HTTP ${response.status}${typeof detail === "string" ? `: ${detail}` : ""}`);
  }
  return response.json();
}

function reset() {
  clearInterval(timer);
  levels.textContent = "Microphone — · Speaker —";
  start.disabled = busy;
  stop.disabled = true;
}

async function memoryTool(name, parameters) {
  try {
    const {result} = await post(`/api/tools/${name}`, {message: parameters.message});
    log("Memory", name === "addMemories" ? result : "Recall completed.");
    return result;
  } catch {
    log("Memory", `${name} failed. No retry was made.`);
    throw new Error("Memory operation failed. Do not claim success.");
  }
}

start.addEventListener("click", async () => {
  if (busy || conversation) return;
  busy = true; cancelled = false;
  start.disabled = true; stop.disabled = false;
  transcript.replaceChildren();
  document.querySelector("#empty").hidden = false;
  status.textContent = "Connecting… allow microphone access if prompted.";
  let stage = "session token", endedDuringStart = false;
  try {
    const {conversationToken} = await post("/api/session");
    if (cancelled) return;
    stage = "microphone / WebRTC connection";
    const session = await Conversation.startSession({
      conversationToken, connectionType: "webrtc", userId: config.userId,
      // Official SDK compatibility option: avoid the v1 publisher-offer join path.
      webRtc: {singlePeerConnection: false},
      workletPaths: {rawAudioProcessor: "/raw-audio-processor.js"},
      clientTools: {
        addMemories: parameters => memoryTool("addMemories", parameters),
        retrieveMemories: parameters => memoryTool("retrieveMemories", parameters),
      },
      onMessage: ({source, message}) => log(source === "user" ? "You" : "Agent", message),
      onModeChange: ({mode}) => { status.textContent = mode === "speaking" ? "Agent speaking · you can interrupt" : "Listening"; },
      onStatusChange: ({status: state}) => {
        if (state === "disconnected") status.textContent = "Session ended";
      },
      onDisconnect: () => { endedDuringStart = true; conversation = undefined; reset(); },
      onError: error => { status.textContent = `Connection error · ${safeError(error)}`; },
    });
    if (cancelled) { await session.endSession(); return; }
    if (endedDuringStart) { await session.endSession(); throw new Error("Disconnected during startup"); }
    conversation = session;
    status.textContent = "Connected · listening";
    timer = setInterval(() => {
      levels.textContent = `Microphone ${Math.round(session.getInputVolume() * 100)}% · Speaker ${Math.round(session.getOutputVolume() * 100)}%`;
    }, 150);
  } catch (error) {
    status.textContent = `Could not start (${stage}) · ${safeError(error)}`;
    reset();
  } finally {
    busy = false;
    if (cancelled) { reset(); status.textContent = "Session ended"; }
    else if (!conversation) reset();
  }
});

stop.addEventListener("click", async () => {
  cancelled = true;
  stop.disabled = true;
  const session = conversation;
  conversation = undefined;
  try { await session?.endSession(); }
  finally {
    reset();
    if (busy) start.disabled = true;
    status.textContent = busy ? "Ending pending connection…" : "Session ended";
  }
});

window.addEventListener("pagehide", () => { cancelled = true; conversation?.endSession(); });

try {
  const response = await fetch("/api/config");
  if (!response.ok) throw new Error();
  config = await response.json();
  start.disabled = false;
  status.textContent = "Ready · microphone is off";
} catch {
  status.textContent = "Local configuration unavailable. Restart the test server.";
}
