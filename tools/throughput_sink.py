"""Minimal HTTP sink for the firmware throughput-test mode.

HISTORICAL / DIAGNOSTIC ONLY:
Binds by default to 0.0.0.0:5000 without authentication to benchmark raw TCP/HTTP
audio throughput from ESP32 boards across a LAN. Route /listen buffers the request
body in memory via request.get_data(cache=False). Intended solely for private LAN
diagnostics; do not expose to public networks.

Listens on 0.0.0.0:5000 and accepts POST /converse (the path the existing
http_client.c targets). Reads the chunked-transfer body, counts bytes,
logs receive rate every second. Returns a minimal valid /converse JSON
so http_client.c parses successfully and reports finish.

Run:
    .venv/Scripts/python.exe tools/throughput_sink.py

No model warmup, no API-key auth, no audio playback. Pure throughput sink.
"""
from flask import Flask, request, jsonify
import time
import sys

app = Flask(__name__)

# Process-global counter so each new connection gets a unique session id
class _Stats:
    sessions = 0

@app.route('/converse', methods=['POST'])
@app.route('/throughput-sink', methods=['POST'])
def sink():
    _Stats.sessions += 1
    sid = _Stats.sessions
    print(f"[sink#{sid}] OPEN from {request.remote_addr}", flush=True)
    start = time.monotonic()
    last_log = start
    bytes_total = 0
    last_bytes = 0
    err = None

    while True:
        try:
            chunk = request.stream.read(4096)
        except Exception as e:
            err = repr(e)
            print(f"[sink#{sid}] READ ERROR: {err}", flush=True)
            break
        if not chunk:
            break
        bytes_total += len(chunk)
        now = time.monotonic()
        if now - last_log >= 1.0:
            rate = (bytes_total - last_bytes) / (now - last_log)
            print(f"[sink#{sid}] t={now-start:5.1f}s  total={bytes_total:>7}B  inst={rate/1024:6.2f} KB/s",
                  flush=True)
            last_log = now
            last_bytes = bytes_total

    duration = time.monotonic() - start
    avg = (bytes_total / duration) if duration > 0 else 0.0
    print(f"[sink#{sid}] DONE  bytes={bytes_total}  dur={duration:.2f}s  avg={avg/1024:.2f} KB/s  err={err}",
          flush=True)

    # Minimal JSON shape http_client.c can parse cleanly.
    return jsonify({
        "transcript": f"sink#{sid}: {bytes_total}B/{duration:.2f}s",
        "reply_text": f"avg {avg/1024:.2f} KB/s",
        "audio_duration_s": 0.0,
        "route": "simple",
        "model": "throughput-sink",
        "auto_escalated": False,
        "stt_s": 0.0,
        "llm_s": 0.0,
        "tts_s": 0.0,
        "total_s": duration,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
        "estimated_cost_usd": 0.0,
    })


@app.route('/listen', methods=['POST'])
def listen_sink():
    # In throughput-test mode the firmware shouldn't /listen at all, but
    # respond cleanly just in case so the client doesn't error out.
    try:
        # Drain whatever the client sends and immediately reply not-wake.
        request.get_data(cache=False)
    except Exception:
        pass
    return jsonify({"wake": False, "reason": "sink-mode"}), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "mode": "throughput-sink", "sessions": _Stats.sessions})


if __name__ == '__main__':
    print("=" * 60, flush=True)
    print("throughput sink listening on 0.0.0.0:5000", flush=True)
    print("=" * 60, flush=True)
    sys.stdout.flush()
    # threaded=True so multiple firmware reconnects don't queue
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False, use_reloader=False)
