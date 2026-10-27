import assert from "node:assert/strict";
import {before, after, test} from "node:test";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createServer} from "node:net";
import {readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {chromium} from "playwright";

const root = fileURLToPath(new URL("../", import.meta.url));
let browser, server, base;
before(async () => {
  const socket = createServer().listen(0, "127.0.0.1");
  await once(socket, "listening");
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  base = `http://127.0.0.1:${port}`;
  server = spawn(`${root}.venv/bin/python`, ["web/mock_server.py", String(port)], {cwd: root, stdio: "ignore"});
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error("Mock server exited");
    try { ready = (await fetch(base)).ok; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(ready);
  browser = await chromium.launch({channel: "chrome", headless: true, args: ["--mute-audio"]});
});
after(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
  if (server && server.exitCode === null) await once(server, "exit");
});

async function pageFor(t, mocked = true) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const source = await readFile(new URL("client.js", import.meta.url), "utf8");
  await context.route("**/*", route => {
    if (!route.request().url().startsWith(base + "/")) return route.abort();
    if (mocked && route.request().url() === base + "/client.js") {
      return route.fulfill({contentType: "text/javascript", body: source.replace('import { Conversation } from "@elevenlabs/client";', "const Conversation = window.sdkMock;")});
    }
    return route.continue();
  });
  await context.addInitScript(() => {
    window.micCalls = 0;
    navigator.mediaDevices.getUserMedia = async () => { window.micCalls++; throw new Error("No physical microphone in checks"); };
    window.sdkStarts = 0; window.sdkEnds = 0; window.muteCalls = 0;
    window.sessionMock = {
      endSession: async () => { window.sdkEnds++; window.sdkOptions.onDisconnect(); },
      getInputVolume: () => 0.25, getOutputVolume: () => 0.5,
      setMicMuted: () => { window.muteCalls++; },
    };
    window.sdkMock = {startSession: async options => {
      window.sdkStarts++; window.sdkOptions = options;
      if (window.denyStart) throw new DOMException("Denied", "NotAllowedError");
      if (window.connectionError) throw new Error(window.connectionError);
      if (window.delayStart) await new Promise(resolve => { window.finishStart = resolve; });
      return window.sessionMock;
    }};
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(base);
  await page.getByRole("button", {name: "Start conversation", exact: true}).waitFor();
  await page.waitForFunction(() => !document.querySelector("#start").disabled);
  return page;
}

test("actual bundled SDK loads without microphone or provider calls", async t => {
  const page = await pageFor(t, false);
  assert.equal(await page.evaluate(() => window.micCalls), 0);
  assert.match(await page.locator("#status").textContent(), /microphone is off/);
  assert.equal(await page.getByRole("button", {name: "End conversation"}).isDisabled(), true);
});

test("official audio worklet loads under the page's content security policy", async t => {
  const page = await pageFor(t, false);
  assert.equal(await page.evaluate(async () => {
    const audio = new AudioContext();
    try {
      await audio.audioWorklet.addModule("/raw-audio-processor.js");
      const node = new AudioWorkletNode(audio, "rawAudioProcessor");
      node.disconnect();
      return true;
    } finally { await audio.close(); }
  }), true);
});

test("Start uses WebRTC and End closes the session without playback muting", async t => {
  const page = await pageFor(t);
  await page.click("#start");
  await page.waitForFunction(() => window.sdkStarts === 1);
  const options = await page.evaluate(() => ({type: window.sdkOptions.connectionType, token: window.sdkOptions.conversationToken, user: window.sdkOptions.userId, webRtc: window.sdkOptions.webRtc}));
  assert.deepEqual(options, {type: "webrtc", token: "fake-session-token", user: "test-user", webRtc: {singlePeerConnection: false}});
  await page.evaluate(() => {
    window.sdkOptions.onModeChange({mode: "speaking"});
    window.sdkOptions.onMessage({source: "user", message: "<img src=x onerror=alert(1)>"});
    window.sdkOptions.onMessage({source: "ai", message: "Hello"});
  });
  assert.match(await page.locator("#status").textContent(), /you can interrupt/);
  assert.equal(await page.locator("#transcript img").count(), 0);
  assert.equal(await page.evaluate(() => window.muteCalls), 0);
  await page.click("#stop");
  assert.equal(await page.evaluate(() => window.sdkEnds), 1);
  assert.equal(await page.locator("#start").isDisabled(), false);
});

test("both memory tools use the existing local bridge", async t => {
  const page = await pageFor(t);
  await page.click("#start");
  await page.waitForFunction(() => window.sdkStarts === 1);
  const results = await page.evaluate(async () => [
    await window.sdkOptions.clientTools.addMemories({message: "Likes teal", user_id: "other"}),
    await window.sdkOptions.clientTools.retrieveMemories({message: "color"}),
  ]);
  assert.match(results[0], /not yet confirmed/);
  assert.equal(results[1], "No memories found.");
});

test("token failure starts no SDK session", async t => {
  const page = await pageFor(t);
  await page.route("**/api/session", route => route.fulfill({status: 502, body: "{}"}));
  await page.click("#start");
  await page.waitForFunction(() => !document.querySelector("#start").disabled);
  assert.equal(await page.evaluate(() => window.sdkStarts), 0);
  assert.match(await page.locator("#status").textContent(), /Could not start/);
});

test("microphone permission failure leaves a retryable page", async t => {
  const page = await pageFor(t);
  await page.evaluate(() => { window.denyStart = true; });
  await page.click("#start");
  await page.waitForFunction(() => !document.querySelector("#start").disabled);
  assert.match(await page.locator("#status").textContent(), /Could not start/);
  assert.match(await page.locator("#status").textContent(), /NotAllowedError/);
  assert.equal(await page.locator("#stop").isDisabled(), true);
});

test("connection failure displays the SDK reason with session tokens redacted", async t => {
  const page = await pageFor(t);
  await page.evaluate(() => { window.connectionError = "signal failed: wss://example.invalid/rtc?access_token=eyJabc.def.ghi&protocol=16"; });
  await page.click("#start");
  await page.waitForFunction(() => !document.querySelector("#start").disabled);
  const message = await page.locator("#status").textContent();
  assert.match(message, /signal failed/);
  assert.match(message, /microphone \/ WebRTC connection/);
  assert.equal(message.includes("eyJabc.def.ghi"), false);
  assert.match(message, /redacted/);
});

test("End during a pending SDK start closes the eventual session", async t => {
  const page = await pageFor(t);
  await page.evaluate(() => { window.delayStart = true; });
  await page.click("#start");
  await page.waitForFunction(() => window.finishStart);
  await page.click("#stop");
  assert.equal(await page.locator("#start").isDisabled(), true);
  await page.evaluate(() => window.finishStart());
  await page.waitForFunction(() => !document.querySelector("#start").disabled);
  assert.equal(await page.evaluate(() => window.sdkEnds), 1);
});
