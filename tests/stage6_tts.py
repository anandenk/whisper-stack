"""Stage 6 verification: Piper TTS synth + system audio playback.

Synthesizes a fixed phrase, plays it through the system default output,
prints timing + RTF, then asks you to confirm you heard it.

Run from repo root after activating .venv:
    python tests/stage6_tts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sounddevice as sd

from server.audio_out import synth_and_play

TEST_PHRASE = (
    "Stage six test successful. "
    "If you can hear this, Piper text to speech is wired up correctly."
)


def main() -> int:
    default_idx = sd.default.device[1]
    default_dev = sd.query_devices(default_idx)
    print(f"Default output device: [{default_idx}] {default_dev['name']}")
    print(f"  device samplerate: {default_dev['default_samplerate']:.0f} Hz")
    print()

    print(f"Synthesizing: {TEST_PHRASE!r}")
    print("(first call also loads the voice model, ~1s)")
    print()

    result = synth_and_play(TEST_PHRASE)
    rtf = result["duration_s"] / result["synth_s"] if result["synth_s"] > 0 else float("inf")

    print("=" * 64)
    print(f"  Voice:           {result['voice']}")
    print(f"  Output sr:       {result['sample_rate']} Hz")
    print(f"  Audio duration:  {result['duration_s']:.2f}s")
    print(f"  Synth time:      {result['synth_s']:.2f}s  (RTF {rtf:.1f}x; >1 = faster than realtime)")
    print(f"  Play time:       {result['play_s']:.2f}s")
    print("=" * 64)
    print()
    print("Did you hear the test phrase through your speakers/headphones?")
    print("If yes, stage 6 is functional. If not, check Windows volume,")
    print("the default output device, and that the device is unmuted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
