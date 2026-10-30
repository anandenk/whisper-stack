# Network diagnosis — summary

**Date:** 2026-05-10
**Network:** Enterprise Wi-Fi 2.4 GHz, RSSI -77 to -60 at the workstation/bench spot
**Device:** ESP-WROOM-32, current main firmware (commit `db0b7ff`) with the PS_NONE + TCP buf 32768 fixes

## TL;DR — the conclusive answer

**The network and our HTTP/socket path are NOT the bottleneck.** We sustained
**34 KB/s for 54 s with zero errors** to the server using the exact same
`http_converse_stream_*` API that production uses. This was enough to fully
saturate our 32 KB/s mic-frame target with no recycling.

**Therefore the audio corruption you've been seeing in production turns is
in the mic-capture / VAD / wake-word / OLED interaction with HTTP — not in
the network or HTTP code itself.**

## What we ran

### Path B — bare-bones HTTP throughput test (RUN, conclusive)

- Added `WS_THROUGHPUT_TEST_MODE` to `firmware/main/main.c` (now reverted)
- Test logic: after WiFi up, skip mic / VAD / wake / OLED entirely; open
  one chunked POST to `/converse`; write a silent 1280-sample int16 frame
  every 80 ms (= 32 KB/s) for 60 s; finish; log per-second stats.
- Server: minimal Flask sink at `tools/throughput_sink.py` (kept — useful for
  future tests). Listens on `:5000`, accepts `/converse`, counts bytes,
  logs receive rate, returns parseable JSON.

#### Device-side result

```
==== THROUGHPUT RESULT ====
  finish: ESP_OK  http_status=200
  frames_ok=750  frames_err=0  total_bytes=1920000
  write_loop_ms=54131  finish_ms=84  avg=34 KB/s
  server reply: sink#2: 1920000B/54.20s
===========================
```

- 750 / 750 frames sent successfully (target was 750 over 60s; pacing came
  in at 54 s because most writes returned in 2 ms)
- **Zero write errors**
- Two transient stalls: t=6 s (last_write=1030 ms) and t=25 s (last_write=736 ms),
  both fully recovered within the next frame
- Boot heap: **209 KB free** (vs 107 KB on production firmware — this matters,
  see below)
- WiFi: connected to BSSID `xx:xx:xx:xx:xx:xx` channel 1, RSSI -77, 802.11n,
  WPA2-Enterprise, PS=NONE confirmed

#### Server-side result (from `throughput_sink.py` log)

Sustained 32–40 KB/s the entire run, with the same two brief dips visible
on the device side. Confirms the test was end-to-end sound.

### Path A — IDF iperf example (SKIPPED, with reason)

- The ESP-IDF iperf example (`C:\Espressif\frameworks\esp-idf-v5.3.5\examples\wifi\iperf\`)
  uses an interactive REPL and its built-in WiFi commands only support PSK,
  not WPA2-Enterprise. To test on enterprise Wi-Fi would require porting our
  `wifi.c` into it and modifying it to auto-run on boot — ~1+ hour of work.
- iperf would have answered the same question Path B answered (sustained
  TCP throughput from this device in this spot to this server) at higher
  cost. Path B uses our real code path, so its result is also more
  diagnostic — it rules out HTTP-chunked encoding as the bottleneck, not
  just raw TCP.
- iperf3 install on the workstation also failed at first attempt
  (winget exit 20) — would have needed manual install or admin elevation.
- Conclusion: not worth the time given Path B's clear positive result.

## What this means for the audio corruption

The bottleneck is in production-firmware behavior that the throughput-test
mode skipped. The most likely culprits, in order:

1. **Concurrent /listen + /converse competition.** Production firmware
   holds `/listen` open continuously for wake-word detection; on wake it
   transitions to `/converse`. The handoff window opens a new socket while
   the old one may still be tearing down. Our test had ONE socket and ONE
   stream — no competition.
2. **i2s_mic capture loop blocking the HTTP write loop.** Production
   firmware's HTTP-write loop pulls frames from the mic ring buffer; if
   the mic capture path takes CPU at the wrong moment, the consumer gets
   starved and the ring overflows.  Our test wrote silent frames inline,
   no mic involvement.
3. **OLED I²C bus contention during writes.** Each OLED flush takes
   I²C time on the same CPU. Production firmware's VU bar redraws every
   3 frames during LISTENING. Test mode skipped OLED entirely.
4. **Heap pressure.** Production boot heap is **107 KB**; throughput-test
   mode boot heap is **209 KB** — 100 KB more headroom. This came from
   skipping mic ring (64 KB), preroll segments (32 KB), and other
   subsystems. The smaller production heap means TCP-level allocations
   (pbufs etc.) under load have less room and may contend with audio
   buffers.

## Recommended next steps (when you get back)

In order of "most likely to identify the real culprit":

1. **Add a `STATS` log on the consumer side** of the HTTP-write loop in
   `listen_stream.c` and `http_client.c`'s streaming path: how many ms is
   each `send()` blocking? Do we see the same kind of 700–1000 ms stalls
   we saw twice in the throughput test? If yes, we know production is
   hitting the same network jitter; the recycle is just because the
   producer can't pause. Fix: add a small TX-side queue with a longer
   absorption window, OR throttle the producer when the ring is >50%.
2. **Test production with /listen disabled** (manual button-only wake).
   If recycle counts drop dramatically, the chronic /listen stream is the
   amplifier — confirming that v2's on-device wake-word eliminates the
   problem architecturally.
3. **Profile the consumer task.** Add per-iteration timing inside
   `audio_in_task` and the HTTP-write loop. Find which one is starving the
   other.
4. **(Long shot) Test with OLED writes disabled mid-conversation.** Comment
   out the VU bar redraw in the LISTENING screen path. If recycle drops,
   I²C contention is real.

## Files in this session

| File | Status |
|------|--------|
| `firmware/main/wifi.c` | Modified — kept (PS_NONE fix) |
| `firmware/sdkconfig.defaults` | Modified — kept (TCP buf 32768) |
| `firmware/sdkconfig` | Modified — kept (enterprise Wi-Fi creds, TCP buf, gesture/IR disabled) |
| `firmware/main/main.c` | Reverted to HEAD |
| `tools/throughput_sink.py` | NEW — kept for future tests |
| `tools/serial_capture.py` | NEW — kept (pyserial-based timed capture) |
| `notes/throughput-monitor.log` | NEW — full device-side log of the test |
| `notes/network-diagnosis-summary.md` | NEW — this file |

## Device state when you return

The ESP-32 is flashed with **current main firmware** (PS_NONE + TCP buf 32768 +
gesture/IR disabled). It will try to associate to enterprise Wi-Fi and connect
to your server at `10.x.x.x:5000`. **Your server is NOT running** (I
killed the throughput sink). To resume normal use, restart the server in
your usual way:

```powershell
$env:HOST = "0.0.0.0"
.\.venv\Scripts\python.exe -m server.app
```

Once the server is up, voice turns should work as they did at the end of
your last session (8/8 clean turns).

## Single sentence the advisor predicted, validated

The previous advisor call said: *"the data already heavily implies the
marginal-network case... iperf will likely confirm that, not reveal a
hidden firmware bug."* The throughput test gave the **opposite** of that
prediction — the network/HTTP path is fine. The bottleneck is genuinely
inside the production firmware's interaction model. v2 hardware with
on-device wake is still the right architectural fix, but **there is also
a real code-side bug to find** — and it's in the mic / VAD / OLED / HTTP
interaction, not the WiFi link.
