// All audio stays in memory. Only an explicit Send action calls a paid endpoint.
import {PCMPlayer, consumeStream} from "/tester/player.js";
const $ = (id) => document.getElementById(id);
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_SECONDS = 30;
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"];
let phase = "checking";
let configured = false;
let session = null;
let permissionVersion = 0;
let recording = null;
let previewURL = null;
let replyURL = null;
let player = null;
let requestController = null;

function render(message) {
  $("capture-state").dataset.phase = phase;
  $("status").textContent = message;
  $("record").textContent = phase === "recording" ? "Stop recording" : "Record";
  $("record").disabled = !configured || ["checking", "permission", "stopping", "sending"].includes(phase);
  $("clear").textContent = phase === "permission" ? "Cancel" : "Clear";
  $("clear").disabled = phase === "checking" || phase === "sending" || phase === "stopping" ||
    (phase === "idle" && !previewURL && !replyURL);
  $("send").disabled = phase !== "review" || !recording;
  document.querySelector(".result").setAttribute("aria-busy", String(phase === "sending"));
}

function error(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

function clearError() {
  $("error").hidden = true;
  $("error").textContent = "";
}

function releaseAudio(element, url) {
  element.pause();
  element.removeAttribute("src");
  element.load();
  if (url) URL.revokeObjectURL(url);
}

function clearReply() {
  player?.close();
  player = null;
  $("mute").hidden = true;
  $("mute").disabled = false;
  $("mute").textContent = "Mute live reply";
  $("sources").replaceChildren();
  $("sources-section").hidden = true;
  releaseAudio($("reply"), replyURL);
  replyURL = null;
  $("reply").hidden = true;
  $("metrics").hidden = true;
  $("diagnostic-note").hidden = true;
  $("diagnostic-link").hidden = true;
  $("diagnostic-link").removeAttribute("href");
  $("reply-status").textContent = "Your reply will appear here after you send a recording.";
  for (const id of ["first-audio", "first-playback", "underruns", "total-time", "stt-time", "llm-time", "tts-time", "tokens", "searches", "cost"]) {
    $(id).textContent = "—";
  }
}

function clearPreview() {
  recording = null;
  releaseAudio($("preview"), previewURL);
  previewURL = null;
  $("preview-section").hidden = true;
}

function releaseMicrophone(capture) {
  clearTimeout(capture.deadline);
  clearInterval(capture.clock);
  capture.stream.getTracks().forEach((track) => track.stop());
}

function stopRecording(discard = false) {
  if (!session) return;
  session.discard ||= discard;
  const capture = session;
  phase = "stopping";
  render("Finishing recording…");
  if (capture.recorder.state !== "inactive") capture.recorder.stop();
  releaseMicrophone(capture);
}

function reset() {
  // Invalidate a pending permission request. Late streams are stopped immediately.
  permissionVersion += 1;
  if (session) {
    stopRecording(true);
    session = null;
  }
  clearPreview();
  clearReply();
  clearError();
  $("clock").textContent = "0:00 / 0:30";
  phase = "idle";
  render(configured ? "Ready when you are." : "Configuration is needed. Reload after setup.");
}

async function startRecording() {
  if (!configured || ["checking", "permission", "stopping", "sending"].includes(phase)) return;
  clearPreview();
  clearReply();
  clearError();
  phase = "permission";
  render("Allow microphone access in your browser to begin.");
  const version = ++permissionVersion;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: true, video: false});
    if (version !== permissionVersion || document.hidden) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, {mimeType, audioBitsPerSecond: 64000});
    const capture = {stream, recorder, chunks: [], bytes: 0, discard: false, started: performance.now()};
    session = capture;
    recorder.addEventListener("dataavailable", ({data}) => {
      if (!data.size || capture.discard) return;
      capture.bytes += data.size;
      if (capture.bytes > MAX_BYTES) {
        capture.failure = "Recording exceeded 10 MiB. Record a shorter question.";
        stopRecording(true);
        return;
      }
      capture.chunks.push(data);
    });
    recorder.addEventListener("error", () => {
      capture.failure = "Recording failed. Check your microphone and try recording again.";
      stopRecording(true);
    });
    recorder.addEventListener("stop", () => {
      releaseMicrophone(capture);
      if (session !== capture) return;
      session = null;
      if (capture.discard) {
        phase = "idle";
        render("Recording discarded. Microphone off.");
        if (capture.failure) error(capture.failure);
        return;
      }
      const blob = new Blob(capture.chunks, {type: recorder.mimeType});
      if (!blob.size) {
        phase = "idle";
        render("No recording captured. Microphone off.");
        error("The recording is empty. Check your microphone and record again.");
        return;
      }
      recording = blob;
      previewURL = URL.createObjectURL(blob);
      $("preview").src = previewURL;
      $("preview-section").hidden = false;
      phase = "review";
      render("Microphone off. Review your recording, then send.");
      $("send").focus({preventScroll: true});
    });
    stream.getAudioTracks().forEach((track) => track.addEventListener("ended", () => {
      if (session === capture && phase === "recording") stopRecording();
    }));
    recorder.start(250);
    phase = "recording";
    render("Recording… tap Stop when you’re finished.");
    $("clock").textContent = "0:00 / 0:30";
    capture.clock = setInterval(() => {
      const seconds = Math.min(MAX_SECONDS, Math.floor((performance.now() - capture.started) / 1000));
      $("clock").textContent = `0:${String(seconds).padStart(2, "0")} / 0:30`;
    }, 250);
    capture.deadline = setTimeout(() => stopRecording(), MAX_SECONDS * 1000);
  } catch (cause) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    if (version !== permissionVersion) return;
    session = null;
    phase = "idle";
    render("Microphone off.");
    const messages = {
      NotAllowedError: "Microphone access was denied. Allow it in your browser’s site settings, then press Record.",
      NotFoundError: "No microphone was found. Connect or enable one, then press Record.",
      NotReadableError: "Your microphone could not be opened. Check system permissions or whether another app is using it.",
    };
    error(messages[cause.name] || "Could not start recording. Check browser microphone support and permissions.");
  }
}

function diagnosticLink(id) {
  if (!id) return null;
  const path = `/diagnostics/${encodeURIComponent(id)}`;
  $("diagnostic-link").href = path;
  $("diagnostic-link").hidden = false;
  return path;
}

async function showDiagnostics(id) {
  const path = diagnosticLink(id);
  if (!path) throw new Error("Missing diagnostic ID");
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error("Diagnostics unavailable");
  const event = await response.json();
  const seconds = (value) => typeof value === "number" && Number.isFinite(value) ? `${(value / 1000).toFixed(2)} s` : "—";
  $("total-time").textContent = seconds(event.total_elapsed_ms);
  $("stt-time").textContent = seconds(event.stt_elapsed_ms);
  $("llm-time").textContent = seconds(event.elapsed_ms);
  $("tts-time").textContent = seconds(event.tts_elapsed_ms);
  $("tokens").textContent = `${event.tokens?.input_tokens ?? "—"} / ${event.tokens?.output_tokens ?? "—"}`;
  $("cost").textContent = event.estimated_cost_usd == null ? "Unknown" : `$${event.estimated_cost_usd}`;
  $("searches").textContent = event.web_search_requests ?? "Unknown";
  $("metrics").hidden = false;
  $("diagnostic-note").textContent = "Estimated cost includes Haiku tokens and search ($0.01 per search), excluding ElevenLabs. First audio is measured in the browser; scheduled playback is an estimate, not a measurement at your speaker. Total processing excludes recording and playback.";
  $("diagnostic-note").hidden = false;
}

async function sendRecording() {
  if (phase !== "review" || !recording) return;
  const blob = recording;
  // Consume once, even on a timeout: retrying could duplicate billable work.
  recording = null;
  $("preview").pause();
  clearError();
  clearReply();
  phase = "sending";
  render("Recognizing speech and preparing a short reply…");
  $("reply-status").textContent = "The reply will play as its audio arrives.";
  const started = performance.now();
  let diagnosticID;
  let submitted = false;
  requestController = new AbortController();
  const deadline = setTimeout(() => requestController?.abort(), 210000);
  try {
    player = new PCMPlayer(started);
    let audioDeadline;
    try {
      await Promise.race([player.ready, new Promise((_, reject) => {
        audioDeadline = setTimeout(() => reject(new Error("Audio could not start. Allow audio playback, then press Send again. No request was sent.")), 5000);
      })]);
    } finally { clearTimeout(audioDeadline); }
    submitted = true;
    const response = await fetch("/converse/stream", {
      method: "POST", headers: {"Content-Type": blob.type.split(";")[0]}, body: blob,
      signal: requestController.signal,
    });
    diagnosticID = response.headers.get("X-Diagnostics-ID");
    diagnosticLink(diagnosticID);
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { /* Use safe fallback. */ }
      throw new Error(typeof detail === "string" ? detail : `Voice request failed (HTTP ${response.status}).`);
    }
    await consumeStream(response, player, (event) => {
      if (event.id !== diagnosticID) throw new Error("Streaming diagnostic ID mismatch.");
      showSources(event.sources);
    }, (audio) => {
      $("reply-status").textContent = audio.muted ? "Live reply muted. Downloading the rest for replay…" : "Playing the reply as audio arrives…";
      render(audio.muted ? "Live reply muted. Finishing the exchange…" : "Playing streamed audio…");
      $("metrics").hidden = false;
      $("mute").hidden = false;
      $("first-audio").textContent = `${(audio.firstReceivedMs / 1000).toFixed(2)} s`;
      $("first-playback").textContent = audio.firstScheduledMs == null ? "—" : `${(audio.firstScheduledMs / 1000).toFixed(2)} s`;
      $("underruns").textContent = String(audio.underruns);
    });
    const audio = player.wav();
    replyURL = URL.createObjectURL(audio);
    $("reply").src = replyURL;
    $("reply").hidden = false;
    $("reply-status").textContent = "All audio received. Replay is available below.";
    phase = "done";
    render("Exchange complete. Record another question when you’re ready.");
  } catch (cause) {
    player?.close();
    if (!submitted) {
      recording = blob;
      phase = "review";
      render("No request sent. Review browser audio permissions before sending.");
      $("reply-status").textContent = "No provider was contacted.";
      error(cause.message);
      return;
    }
    phase = "error";
    render("Request stopped. No automatic retry was made.");
    $("reply-status").textContent = "No playable reply received.";
    error(["TimeoutError", "AbortError", "TypeError"].includes(cause.name)
      ? "The request timed out or the connection was lost. It may still incur charges. Check usage diagnostics before sending another question."
      : cause.message);
  } finally {
    clearTimeout(deadline);
    requestController = null;
  }
  // Keep controls locked until diagnostics settle, so stale results cannot overwrite a new exchange.
  const finishedPhase = phase;
  phase = "sending";
  render($("status").textContent);
  try {
    await showDiagnostics(diagnosticID);
  } catch {
    $("diagnostic-note").textContent = "Exchange diagnostics are unavailable here. Check Usage diagnostics below; missing usage does not mean the request was free.";
    $("diagnostic-note").hidden = false;
  }
  phase = finishedPhase;
  render($("status").textContent);
}

function showSources(sources) {
  if (!Array.isArray(sources)) return;
  for (const source of sources.slice(0, 10)) {
    try {
      const url = new URL(source.url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) continue;
      const link = document.createElement("a");
      link.href = url.href;
      link.textContent = source.title || url.hostname;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const item = document.createElement("li");
      item.append(link);
      $("sources").append(item);
    } catch { /* Ignore malformed citation URLs. */ }
  }
  $("sources-section").hidden = !$("sources").children.length;
}

$("record").addEventListener("click", () => phase === "recording" ? stopRecording() : startRecording());
$("clear").addEventListener("click", reset);
$("send").addEventListener("click", sendRecording);
$("mute").addEventListener("click", () => {
  player?.mute();
  $("mute").textContent = "Live reply muted";
  $("mute").disabled = true;
});
$("reply").addEventListener("play", () => player?.mute());
function leaveCapture() {
  if (phase === "permission") reset();
  if (session) stopRecording(true);
}
document.addEventListener("visibilitychange", () => { if (document.hidden) leaveCapture(); });
window.addEventListener("pagehide", () => {
  requestController?.abort();
  leaveCapture();
  clearPreview();
  clearReply();
});

async function initialize() {
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !window.AudioContext || !MIME_TYPES.some((type) => MediaRecorder.isTypeSupported(type))) {
      throw new Error("Recording is unavailable in this browser. Open this local page in a current Chrome or Safari browser.");
    }
    const response = await fetch("/health", {cache: "no-store", signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error("Could not check the backend. Start it, then reload this page.");
    const health = await response.json();
    configured = health.api_key_configured && health.voice_configured && health.anthropic_key_format_valid;
    if (!configured) throw new Error("Configure ElevenLabs, a voice, and Anthropic in backend/.env, restart the backend, then reload. No provider was contacted.");
    phase = "idle";
    render("Ready when you are.");
  } catch (cause) {
    phase = "error";
    render("Setup needed before recording.");
    error(cause.message);
  }
}
initialize();
