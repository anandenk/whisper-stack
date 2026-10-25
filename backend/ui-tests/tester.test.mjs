import assert from "node:assert/strict";
import {after, before, test} from "node:test";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createServer} from "node:net";
import {fileURLToPath} from "node:url";
import {chromium} from "playwright";

const root = fileURLToPath(new URL("../../", import.meta.url));
let browser, server, base;
let serverErrors = "";

before(async () => {
  const socket = createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  base = `http://127.0.0.1:${port}`;
  server = spawn(`${root}/backend/.venv/bin/python`, ["backend/ui-tests/server.py", String(port)], {cwd: root, stdio: ["ignore", "ignore", "pipe"]});
  server.stderr.on("data", (chunk) => { serverErrors += chunk; });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Test server exited: ${serverErrors}`);
    try { ready = (await fetch(`${base}/health`)).ok; } catch { /* Server is starting. */ }
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(ready, "Mock backend started");
  browser = await chromium.launch({channel: process.env.TEST_BROWSER_CHANNEL || "chrome", headless: true,
    args: ["--autoplay-policy=no-user-gesture-required"]});
}, {timeout: 30000});

after(async () => {
  await browser?.close();
  server?.kill("SIGTERM");
  if (server && server.exitCode === null) await once(server, "exit");
  assert.equal(serverErrors.includes("Traceback"), false, serverErrors);
});

async function pageFor(t, mode = "synthetic") {
  const context = await browser.newContext();
  t.after(() => context.close());
  // Block any unexpected nonlocal traffic. No real microphone or provider access.
  await context.route("**/*", (route) => route.request().url().startsWith(base + "/") ? route.continue() : route.abort());
  await context.addInitScript((mode) => {
    window.micCalls = 0;
    window.testTracks = [];
    window.testContexts = [];
    async function syntheticStream() {
      const audio = new AudioContext();
      const oscillator = audio.createOscillator();
      const destination = audio.createMediaStreamDestination();
      oscillator.connect(destination); // Never connected to a physical speaker.
      oscillator.start();
      const track = destination.stream.getTracks()[0];
      const stop = track.stop.bind(track);
      track.stop = () => {
        if (track.readyState === "ended") return;
        stop(); oscillator.stop(); audio.close();
      };
      window.testTracks.push(track);
      window.testContexts.push(audio);
      await audio.resume();
      return destination.stream;
    }
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {value: async () => {
      window.micCalls += 1;
      if (mode === "denied") throw new DOMException("Denied by test", "NotAllowedError");
      if (mode === "pending") return new Promise((resolve) => { window.resolveMicrophone = () => resolve(syntheticStream()); });
      return syntheticStream();
    }});
    if (mode === "unsupported") Object.defineProperty(window, "MediaRecorder", {value: undefined});
  }, mode);
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], "No uncaught browser errors"));
  return page;
}

async function ready(page) {
  await page.goto(`${base}/tester`);
  await page.waitForFunction(() => !document.getElementById("record").disabled);
}

async function record(page) {
  await page.getByRole("button", {name: "Record", exact: true}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "recording");
  await page.waitForTimeout(700); // Allow real MediaRecorder to emit a synthetic chunk.
  await page.getByRole("button", {name: "Stop recording"}).click();
  try {
    await page.waitForFunction(() => !document.getElementById("send").disabled);
  } catch {
    throw new Error(await page.locator(".recorder").innerText());
  }
  assert.ok(await page.evaluate(() => window.testTracks.every((track) => track.readyState === "ended")));
}

test("page load is free; synthetic recording traverses the mocked provider pipeline once", async (t) => {
  const page = await pageFor(t);
  let posts = 0;
  page.on("request", (request) => { if (request.method() === "POST") posts += 1; });
  await ready(page);
  assert.equal(await page.evaluate(() => window.micCalls), 0);
  assert.equal(posts, 0);
  await record(page);
  assert.equal(posts, 0, "Review does not upload audio");
  await page.evaluate(() => { const button = document.getElementById("send"); button.click(); button.click(); });
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "done");
  assert.equal(posts, 1, "Double click cannot duplicate paid work");
  assert.equal(await page.locator("#reply").isVisible(), true);
  assert.equal(await page.locator("#tokens").innerText(), "100 / 20");
  assert.equal(await page.locator("#cost").innerText(), "$0.000020000");
  assert.notEqual(await page.locator("#llm-time").innerText(), "—");
  assert.match(await page.locator("#diagnostic-link").getAttribute("href"), /^\/diagnostics\//);
  await page.getByRole("button", {name: "Clear", exact: true}).click();
  assert.equal(await page.locator("#reply").getAttribute("src"), null);
  assert.equal(await page.locator("#preview").getAttribute("src"), null);
  assert.equal(await page.locator("#diagnostic-link").getAttribute("href"), null);
});

test("denied microphone is actionable and never sends a question", async (t) => {
  const page = await pageFor(t, "denied");
  await ready(page);
  await page.getByRole("button", {name: "Record", exact: true}).click();
  await page.waitForFunction(() => !document.getElementById("error").hidden);
  assert.match(await page.locator("#error").innerText(), /access was denied/);
  assert.equal(await page.locator("#send").isEnabled(), false);
});

test("cancelling pending permission stops a late stream without starting recording", async (t) => {
  const page = await pageFor(t, "pending");
  await ready(page);
  await page.getByRole("button", {name: "Record", exact: true}).click();
  await page.getByRole("button", {name: "Cancel", exact: true}).click();
  await page.evaluate(() => window.resolveMicrophone());
  await page.waitForFunction(() => window.testTracks.length === 1 && window.testTracks[0].readyState === "ended");
  assert.equal(await page.locator("#capture-state").getAttribute("data-phase"), "idle");
  assert.equal(await page.locator("#preview-section").isVisible(), false);
});

test("leaving the visible tab discards recording and releases the microphone", async (t) => {
  const page = await pageFor(t);
  await ready(page);
  await page.getByRole("button", {name: "Record", exact: true}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "recording");
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {value: true, configurable: true});
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "idle");
  assert.ok(await page.evaluate(() => window.testTracks.every((track) => track.readyState === "ended")));
  assert.equal(await page.locator("#preview-section").isVisible(), false);
});

test("30-second cap stops capture for review without uploading", async (t) => {
  const page = await pageFor(t);
  // Speed up only the capture deadline, keeping MediaRecorder and all other timers real.
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = (fn, delay, ...args) => original(fn, delay === 30000 ? 600 : delay, ...args);
  });
  await ready(page);
  await page.getByRole("button", {name: "Record", exact: true}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "review");
  assert.ok(await page.evaluate(() => window.testTracks.every((track) => track.readyState === "ended")));
});

test("missing configuration and unsupported browsers never request a microphone", async (t) => {
  for (const mode of ["synthetic", "unsupported"]) {
    const page = await pageFor(t, mode);
    await page.route("**/health", (route) => route.fulfill({json: {api_key_configured: false}}));
    await page.goto(`${base}/tester`);
    await page.waitForFunction(() => !document.getElementById("error").hidden);
    assert.equal(await page.locator("#record").isEnabled(), false);
    assert.equal(await page.evaluate(() => window.micCalls), 0);
  }
});

test("provider failure retains linked diagnostics and never retries", async (t) => {
  const page = await pageFor(t);
  let calls = 0;
  await page.route("**/converse", (route) => {
    calls += 1;
    return route.fulfill({status: 502, json: {detail: "Speech synthesis failed."}, headers: {"X-Diagnostics-ID": "failed-test"}});
  });
  await page.route("**/diagnostics/failed-test", (route) => route.fulfill({json: {tokens: {input_tokens: 100, output_tokens: 20}, estimated_cost_usd: "0.000020000"}}));
  await ready(page);
  await record(page);
  await page.getByRole("button", {name: "Send question"}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "error");
  assert.match(await page.locator("#error").innerText(), /Speech synthesis failed/);
  assert.equal(await page.locator("#cost").innerText(), "$0.000020000");
  assert.equal(await page.locator("#send").isEnabled(), false);
  assert.equal(calls, 1);
});

test("a lost connection warns about possible charges without retrying", async (t) => {
  const page = await pageFor(t);
  let calls = 0;
  await page.route("**/converse", (route) => { calls += 1; return route.abort("failed"); });
  await ready(page);
  await record(page);
  await page.getByRole("button", {name: "Send question"}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "error");
  assert.match(await page.locator("#error").innerText(), /may still incur charges/);
  assert.equal(calls, 1);
});

test("diagnostic failure does not discard a successful audio reply", async (t) => {
  const page = await pageFor(t);
  await page.route("**/diagnostics/*", (route) => route.fulfill({status: 503}));
  await ready(page);
  await record(page);
  await page.getByRole("button", {name: "Send question"}).click();
  await page.waitForFunction(() => document.getElementById("capture-state").dataset.phase === "done");
  assert.equal(await page.locator("#reply").isVisible(), true);
  assert.match(await page.locator("#diagnostic-note").innerText(), /diagnostics are unavailable/);
});

test("desktop and narrow layouts retain visible controls without horizontal overflow", async (t) => {
  const page = await pageFor(t);
  await ready(page);
  for (const width of [320, 375, 414, 768, 1280]) {
    await page.setViewportSize({width, height: 900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No overflow at ${width}px`);
    await record(page);
    for (const id of ["record", "clear", "send"]) {
      assert.ok(await page.locator(`#${id}`).evaluate((element) => element.scrollWidth <= element.clientWidth), `${id} fits at ${width}px`);
    }
    await page.getByRole("button", {name: "Clear", exact: true}).click();
  }
  if (process.env.UI_SCREENSHOT) await page.screenshot({path: process.env.UI_SCREENSHOT, fullPage: true});
});
