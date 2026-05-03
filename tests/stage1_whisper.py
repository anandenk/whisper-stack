"""Stage 1 verification: faster-whisper on the GPU.

Loads large-v3-turbo on CUDA, transcribes samples/jfk.wav, prints model info,
transcription, processing time, and real-time factor (RTF = audio_dur / proc_time).

Run from repo root after activating .venv:
    python tests/stage1_whisper.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# CTranslate2 wheels on Windows do NOT bundle CUDA DLLs the way Linux wheels do.
# Add NVIDIA's pip-installed cuBLAS/cuDNN DLL dirs to the search path before
# importing faster_whisper, otherwise the encoder fails with
# "Library cublas64_12.dll is not found or cannot be loaded".
_nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
if _nvidia_root.exists():
    for _bin_dir in _nvidia_root.glob("*/bin"):
        os.add_dll_directory(str(_bin_dir))

from faster_whisper import WhisperModel


def main() -> int:
    sample = Path(__file__).resolve().parent.parent / "samples" / "jfk.wav"
    if not sample.exists():
        print(f"ERROR: sample audio not found at {sample}", file=sys.stderr)
        return 1

    model_name = "large-v3-turbo"
    device = "cuda"
    compute_type = "float16"

    print(f"Loading model: {model_name} on {device} ({compute_type})...")
    t0 = time.perf_counter()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:
        print(f"FATAL: model load failed on {device}: {exc!r}", file=sys.stderr)
        print("If this is a CUDA/Blackwell error, fall back to openai-whisper.", file=sys.stderr)
        return 2
    load_time = time.perf_counter() - t0
    print(f"Model loaded in {load_time:.2f}s")

    print(f"Transcribing {sample.name}...")
    t0 = time.perf_counter()
    segments, info = model.transcribe(str(sample), beam_size=5)
    segments = list(segments)  # force generator -> list so timing is accurate
    elapsed = time.perf_counter() - t0

    text = " ".join(s.text.strip() for s in segments).strip()
    audio_duration = float(info.duration)
    rtf = audio_duration / elapsed if elapsed > 0 else float("inf")

    print()
    print("=" * 64)
    print(f"  Model:           {model_name}")
    print(f"  Device:          {device} ({compute_type})")
    print(f"  Audio:           {sample.name} ({audio_duration:.2f}s)")
    print(f"  Detected lang:   {info.language} (prob {info.language_probability:.2f})")
    print(f"  Transcription:   {text}")
    print(f"  Process time:    {elapsed:.3f}s")
    print(f"  Model load time: {load_time:.2f}s (one-time)")
    print(f"  RTF:             {rtf:.2f}x  (>1 = faster than realtime)")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
