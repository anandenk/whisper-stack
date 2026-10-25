// PCM is played as chunks arrive. Only the final replay needs a WAV container.
export class PCMPlayer {
  constructor(started) {
    this.context = new AudioContext({latencyHint: "interactive"});
    this.ready = this.context.resume();
    this.started = started;
    this.nextTime = 0;
    this.chunks = [];
    this.bytes = 0;
    this.nodes = new Set();
    this.muted = false;
    this.firstReceivedMs = null;
    this.firstScheduledMs = null;
    this.underruns = 0;
  }

  append(encoded) {
    const binary = atob(encoded);
    if (!binary.length || binary.length % 2 || this.bytes + binary.length > 8 * 1024 * 1024) {
      throw new Error("Invalid or oversized streamed audio.");
    }
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    this.bytes += bytes.length;
    this.chunks.push(bytes);
    this.firstReceivedMs ??= performance.now() - this.started;
    if (this.muted) return;
    if (this.context.state !== "running") throw new Error("Audio playback was suspended. Check browser audio permissions.");
    const buffer = this.context.createBuffer(1, bytes.length / 2, 16000);
    const samples = buffer.getChannelData(0);
    const data = new DataView(bytes.buffer);
    for (let i = 0; i < samples.length; i += 1) samples[i] = data.getInt16(i * 2, true) / 32768;
    const node = this.context.createBufferSource();
    node.buffer = buffer;
    node.connect(this.context.destination);
    if (this.nextTime && this.nextTime < this.context.currentTime) this.underruns += 1;
    const when = Math.max(this.nextTime, this.context.currentTime + (this.nextTime > this.context.currentTime ? 0 : 0.08));
    this.firstScheduledMs ??= performance.now() - this.started + (when - this.context.currentTime) * 1000;
    this.nextTime = when + buffer.duration;
    this.nodes.add(node);
    node.onended = () => { node.disconnect(); this.nodes.delete(node); };
    node.start(when);
  }

  mute() {
    this.muted = true;
    for (const node of this.nodes) node.stop();
  }

  close() {
    this.mute();
    if (this.context.state !== "closed") this.context.close().catch(() => {});
  }

  wav() {
    if (!this.bytes) throw new Error("The reply contained no audio.");
    const header = new ArrayBuffer(44);
    const view = new DataView(header);
    const write = (offset, text) => [...text].forEach((character, i) => view.setUint8(offset + i, character.charCodeAt(0)));
    write(0, "RIFF"); view.setUint32(4, 36 + this.bytes, true); write(8, "WAVEfmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    write(36, "data"); view.setUint32(40, this.bytes, true);
    return new Blob([header, ...this.chunks], {type: "audio/wav"});
  }
}

export async function consumeStream(response, player, onStart, onAudio) {
  if (response.headers.get("Content-Type")?.split(";")[0] !== "application/x-ndjson" || !response.body) {
    throw new Error("The backend returned an unexpected streaming format.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "", started = false, done = false;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      pending += decoder.decode(chunk.value, {stream: true});
      let boundary;
      while ((boundary = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, boundary);
        pending = pending.slice(boundary + 1);
        if (line.length > 65536 || done) throw new Error("Invalid streaming event.");
        const event = JSON.parse(line);
        if (!started) {
          if (event.type !== "start" || event.encoding !== "pcm_s16le" || event.sample_rate !== 16000 || event.channels !== 1) {
            throw new Error("Unsupported streaming audio format.");
          }
          started = true;
          onStart(event);
        } else if (event.type === "audio" && typeof event.pcm === "string") {
          player.append(event.pcm);
          onAudio(player);
        } else if (event.type === "done") {
          done = true;
        } else if (event.type === "error") {
          throw new Error(typeof event.detail === "string" ? event.detail : "Audio stream failed.");
        } else {
          throw new Error("Unexpected streaming event.");
        }
      }
      if (pending.length > 65536) throw new Error("Streaming event exceeded the local size limit.");
    }
    pending += decoder.decode();
    if (!done || pending.trim()) throw new Error("Audio stream was interrupted. Usage may still have been billed.");
  } catch (error) {
    player.close();
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}
