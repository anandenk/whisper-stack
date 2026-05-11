"""Analyze the per-turn WAVs + serial monitor log produced by a
loopback-test run, and write a verdict to notes/loopback-test-results.md.

Inputs:
  samples/_loopback_turn_<unix-ts>.wav   (post-normalize, per-turn)
  samples/_loopback_turn_<unix-ts>.bin   (raw int16 pre-normalize, per-turn)
  notes/loopback-iterations.jsonl        (stimulus log from loopback_test.py)
  notes/loopback-monitor.log             (serial output from serial_capture.py)
  samples/jfk.wav                        (reference passage)

Output:
  notes/loopback-test-results.md         (verdict + per-turn table)
  notes/loopback-reference-transcript.txt (one-time, cached)

Verdict logic (from gotcha #9 in CLAUDE.md, advisor-confirmed):
  Primary signal:   recycled count from `STATS[vad] ... recycled=N` lines.
  Secondary signal: received WAV duration shortfall vs expected.
  Tertiary signal:  whisper WER on the received audio vs jfk.wav transcript.

WER alone isn't enough to call a verdict — the loopback adds its own
acoustic noise (speakers → room → INMP441 → 24→16 shift → whisper), so
some WER on a perfectly clean turn is normal. Recycle is ground-truth
from the producer side; duration shortfall is direct evidence of
frames dropped.

Usage:
  .venv/Scripts/python.exe tools/loopback_analyze.py
"""

from __future__ import annotations

import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SAMPLES_DIR = REPO_ROOT / "samples"
NOTES_DIR = REPO_ROOT / "notes"
ITERATIONS_LOG = NOTES_DIR / "loopback-iterations.jsonl"
MONITOR_LOG = NOTES_DIR / "loopback-monitor.log"
RESULTS_MD = NOTES_DIR / "loopback-test-results.md"
REFERENCE_TRANSCRIPT = NOTES_DIR / "loopback-reference-transcript.txt"
JFK_WAV = SAMPLES_DIR / "jfk.wav"


# ESP-IDF logs lines like:
#   STATS[vad] ... recycled=3 (or recycled=0)
# Captures any digit count. Anchored to "STATS[vad]" so we don't match
# unrelated mentions of "recycled" in other log lines.
STATS_RE = re.compile(r"STATS\[vad\][^\n]*?recycled=(\d+)")
# Wake events from main.c — used to count wake fires per iteration
WAKE_RE = re.compile(r"\bWAKE\b|wakeword .*: detected|wake.*detected", re.IGNORECASE)


def read_wav_int16(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        rate = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(n)
    if sw != 2:
        return np.zeros(0, dtype=np.int16), rate
    pcm = np.frombuffer(raw, dtype=np.int16)
    if ch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return pcm, rate


def find_turn_wavs() -> list[Path]:
    wavs = sorted(SAMPLES_DIR.glob("_loopback_turn_*.wav"))
    return wavs


def ts_from_wav_name(p: Path) -> int:
    """samples/_loopback_turn_1234567890.wav -> 1234567890"""
    m = re.search(r"_loopback_turn_(\d+)\.wav$", p.name)
    return int(m.group(1)) if m else 0


def parse_stats_lines(monitor_log: Path) -> list[tuple[int, int]]:
    """Return ordered list of (line_index, recycled) from the monitor log."""
    if not monitor_log.exists():
        return []
    out: list[tuple[int, int]] = []
    with open(monitor_log, "r", encoding="utf-8", errors="replace") as fp:
        for i, line in enumerate(fp):
            m = STATS_RE.search(line)
            if m:
                out.append((i, int(m.group(1))))
    return out


def parse_iterations_log(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def get_reference_transcript() -> str:
    """Whisper-transcribe jfk.wav once; cache the result for re-runs."""
    if REFERENCE_TRANSCRIPT.exists():
        return REFERENCE_TRANSCRIPT.read_text(encoding="utf-8").strip()
    print("[analyze] transcribing reference jfk.wav...", flush=True)
    from server import modes
    result = modes.get_stt().transcribe(JFK_WAV)
    text = result["transcript"].strip()
    REFERENCE_TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_TRANSCRIPT.write_text(text + "\n", encoding="utf-8")
    print(f"[analyze] reference transcript: {text!r}", flush=True)
    return text


def transcribe_wav(path: Path) -> str:
    from server import modes
    try:
        result = modes.get_stt().transcribe(path)
        return result["transcript"].strip()
    except Exception as exc:
        return f"<transcribe error: {exc!r}>"


def normalize_text(s: str) -> list[str]:
    """Whisper-friendly normalization: lowercase, strip punctuation, split."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.split()


def word_error_rate(ref: str, hyp: str) -> float:
    """Levenshtein-based WER. No external dep."""
    r = normalize_text(ref)
    h = normalize_text(hyp)
    if not r:
        return float("nan")
    # Distance over word tokens
    n, m = len(r), len(h)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[n][m] / n


def analyze_one_turn(wav_path: Path, ref_transcript: str) -> dict:
    pcm, rate = read_wav_int16(wav_path)
    dur = len(pcm) / rate if rate else 0.0
    peak = int(np.abs(pcm).max()) if pcm.size else 0
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if pcm.size else 0.0
    hyp = transcribe_wav(wav_path)
    wer = word_error_rate(ref_transcript, hyp)
    return {
        "wav": wav_path.name,
        "ts": ts_from_wav_name(wav_path),
        "duration_s": dur,
        "sample_rate": rate,
        "peak": peak,
        "rms": rms,
        "transcript": hyp,
        "wer": wer,
    }


def render_report(turns: list[dict], stats: list[tuple[int, int]],
                  iters: list[dict], ref_transcript: str,
                  monitor_present: bool) -> str:
    out: list[str] = []
    out.append("# Loopback test — verdict on audio corruption (network vs firmware)\n")
    out.append("**Method.** Acoustic loopback: workstation speakers play "
               "`hey jarvis` followed by `samples/jfk.wav` (11 s); device's mic "
               "captures, VAD records, firmware POSTs to `/converse`; server "
               "saves each upload to `samples/_loopback_turn_<unix-ts>.wav`. "
               "Repeat N iterations with a long fixed gap so the reply has "
               "time to finish.\n")

    out.append("**Discriminator (from gotcha #9, advisor-confirmed).** "
               "Primary: `recycled=N` count from firmware STATS lines. "
               "Secondary: received-WAV duration shortfall (frames recycled "
               "= WAV shorter than expected). Tertiary: WER (loopback adds "
               "its own acoustic noise, so some WER on clean turns is normal).\n")

    out.append(f"**Reference transcript (whisper on `samples/jfk.wav`):** "
               f"`{ref_transcript}`\n")
    ref_dur = 0.0
    if JFK_WAV.exists():
        pcm, sr = read_wav_int16(JFK_WAV)
        ref_dur = len(pcm) / sr if sr else 0.0
    out.append(f"**Expected received duration** (passage only, no prefix): "
               f"**{ref_dur:.2f} s** ± VAD silence-dwell (~2.5 s tail).\n")
    out.append(f"**Iterations attempted:** {len(iters)}  "
               f"**Turns received by server:** {len(turns)}  "
               f"**Wake reliability:** "
               f"{(len(turns) / max(1, len(iters)) * 100):.0f}%\n")

    if turns:
        recycled_counts = []
        # Pair each turn with the nearest STATS line by sequential index.
        # turns are sorted by ts; stats by line order; they should be the
        # same length when wake reliability is 100%. When wakes miss, the
        # counts diverge — we still pair by order and surface the gap.
        n = min(len(turns), len(stats))
        out.append("\n## Per-turn metrics\n")
        out.append("| # | recv-dur (s) | peak | rms | recycled | WER | transcript (first 80c) |")
        out.append("|---|---|---|---|---|---|---|")
        for i, t in enumerate(turns):
            rec = stats[i][1] if i < len(stats) else "—"
            if isinstance(rec, int):
                recycled_counts.append(rec)
            tx = (t["transcript"] or "").replace("|", "\\|").replace("\n", " ")
            if len(tx) > 80:
                tx = tx[:77] + "..."
            wer_disp = f"{t['wer']:.2f}" if t["wer"] == t["wer"] else "—"
            out.append(f"| {i+1} | {t['duration_s']:.2f} | {t['peak']} | "
                       f"{t['rms']:.0f} | {rec} | {wer_disp} | {tx} |")
        out.append("")

        # Aggregate
        durs = [t["duration_s"] for t in turns]
        wers = [t["wer"] for t in turns if t["wer"] == t["wer"]]
        if recycled_counts:
            recycle_avg = sum(recycled_counts) / len(recycled_counts)
            recycle_max = max(recycled_counts)
            recycle_total = sum(recycled_counts)
        else:
            recycle_avg = recycle_max = recycle_total = 0
        avg_dur = sum(durs) / len(durs) if durs else 0
        wer_avg = sum(wers) / len(wers) if wers else float("nan")

        out.append(f"\n## Aggregate\n")
        out.append(f"- Avg received duration: **{avg_dur:.2f} s**  "
                   f"(expected ≈ {ref_dur:.2f} s + ~2.5 s VAD tail)")
        out.append(f"- Avg WER: **{wer_avg:.2f}**")
        out.append(f"- Recycled (firmware producer-side frame drops): "
                   f"avg **{recycle_avg:.1f}/turn**, "
                   f"max **{recycle_max}**, total **{recycle_total}**")
        out.append(f"- Monitor log present: {monitor_present}  "
                   f"STATS lines parsed: {len(stats)}")
        out.append("")

        # Verdict
        # Shortfall threshold: device should give us at least the passage,
        # minus generous slack for VAD-start lag. Anything <80% of expected is
        # a strong signal of frames dropped or capture cut short.
        expected = ref_dur
        short_turns = sum(1 for d in durs if d < 0.8 * expected) if expected else 0
        garbled_turns = sum(1 for w in wers if w > 0.5)

        out.append("\n## Verdict\n")
        verdict = []

        if not stats and turns:
            verdict.append(
                "**Inconclusive on the primary signal:** no `STATS[vad] ... "
                "recycled=N` lines were found in the monitor log. Either the "
                "serial monitor capture missed them, or the firmware in this "
                "build is not logging STATS for this code path. Falling back "
                "to the secondary (WAV duration) and tertiary (WER) signals "
                "only — verdict is one tier weaker than it would be with STATS.")

        if recycle_avg > 5 and (short_turns or garbled_turns):
            verdict.append(
                "**NETWORK / firmware-producer verdict.** Recycled avg "
                f"{recycle_avg:.1f}/turn (max {recycle_max}) AND received "
                f"WAVs are short/garbled ({short_turns} short turns, "
                f"{garbled_turns} high-WER turns). Frames are being dropped "
                "by the producer (mic capture → ring buffer) before HTTP can "
                "carry them. The throughput-test result still holds — HTTP "
                "transit is fine — so the loss is in the firmware's "
                "consumer/producer scheduling, not in the wire. Most likely "
                "the consumer (HTTP-write loop) is being starved by mic / "
                "VAD / OLED tasks during sustained capture.")
        elif recycle_avg < 2 and (short_turns >= len(turns) // 2 or garbled_turns):
            verdict.append(
                "**SOFTWARE (non-recycle) verdict.** Recycle counts are low "
                f"(avg {recycle_avg:.1f}/turn) but received audio is wrong "
                f"({short_turns} short turns, {garbled_turns} high-WER turns). "
                "Frames arrived intact according to the producer, yet the "
                "WAV content is corrupted. Look at: VAD cutting recording "
                "short (silence-dwell tripping mid-passage), wrong "
                "endianness/sign on a mic-capture path, server-side "
                "normalization clipping or amplifying garbage, or a session "
                "ordering bug between /listen and /converse causing the "
                "wrong bytes to be sent.")
        elif recycle_avg > 5 and not (short_turns or garbled_turns):
            verdict.append(
                "**MIXED:** firmware is recycling frames "
                f"(avg {recycle_avg:.1f}/turn) but the received audio still "
                "transcribes cleanly. The producer-side loss isn't large "
                "enough to corrupt the recording — the consumer is keeping "
                "up well enough that the survival rate is high. Watch for "
                "this degrading further as RSSI drops or other tasks are "
                "added.")
        elif recycle_avg < 2 and not (short_turns or garbled_turns):
            verdict.append(
                "**CLEAN PASS.** No recycled frames, full-duration received "
                "audio, clean transcripts. The corruption the user has been "
                "seeing in production is NOT reproducing under loopback-driven "
                "stimulation. Possible reasons: (a) acoustic stimulus is more "
                "predictable than human speech (no long pauses that trip "
                "VAD); (b) test pacing avoids a real interaction collision "
                "the user has been running into; (c) the issue is intermittent "
                "and didn't fire in this 10-iteration window. Worth a manual "
                "voice-turn re-test before concluding.")
        else:
            verdict.append(
                "**HYBRID / nuanced:** some recycle, some short turns, some "
                "high WER — no single mode dominates. List of indicators "
                "above; pick the strongest one and follow that thread first.")

        out.extend(verdict)
        out.append("")

    else:
        out.append("\n## No turns received\n")
        out.append("Server did not receive any /converse uploads during the "
                   "test window. This means either:")
        out.append("- The wake word never fired (check the monitor log for "
                   "`WAKE`); acoustic stimulus may have been too quiet or "
                   "Piper's pronunciation of `hey jarvis` may not trigger "
                   "openWakeWord well.")
        out.append("- The device wasn't online (check `wifi:` lines in the "
                   "monitor log).")
        out.append("- `LOOPBACK_KEEP_PER_TURN=1` was not set in the server "
                   "env (no per-turn WAVs would have been saved).")
        out.append("")

    # Always include the iterations + monitor head/tail for forensics
    if iters:
        out.append("\n## Iteration log (first 3, last 1)\n")
        out.append("```json")
        for it in iters[:3]:
            out.append(json.dumps(it))
        if len(iters) > 3:
            out.append("...")
            out.append(json.dumps(iters[-1]))
        out.append("```\n")

    return "\n".join(out)


def main():
    if not JFK_WAV.exists():
        print(f"[analyze] missing reference: {JFK_WAV}", file=sys.stderr)
        sys.exit(1)

    monitor_present = MONITOR_LOG.exists()
    iters = parse_iterations_log(ITERATIONS_LOG)
    stats = parse_stats_lines(MONITOR_LOG) if monitor_present else []
    wavs = find_turn_wavs()

    print(f"[analyze] iterations log entries: {len(iters)}", flush=True)
    print(f"[analyze] turn WAVs: {len(wavs)}", flush=True)
    print(f"[analyze] STATS lines in monitor: {len(stats)}", flush=True)

    ref_transcript = get_reference_transcript() if wavs else ""

    turns: list[dict] = []
    for w in wavs:
        print(f"[analyze] {w.name}...", flush=True)
        turns.append(analyze_one_turn(w, ref_transcript))

    report = render_report(turns, stats, iters, ref_transcript, monitor_present)
    RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_MD.write_text(report, encoding="utf-8")
    print(f"[analyze] wrote {RESULTS_MD}", flush=True)


if __name__ == "__main__":
    main()
