"""Centralized config + .env loading.

Imported first by every other server module. `load_dotenv(override=True)` is
mandatory: an empty ANTHROPIC_API_KEY exists in the user's Windows env vars,
and without override the real key in .env is silently ignored.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Use 127.0.0.1 not 'localhost' on Windows — DNS/IPv6 fallback adds ~2s per request.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_EMBED_URL = f"{OLLAMA_HOST}/api/embed"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b-instruct-q5_K_M")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Router: simple wins only when it beats complex by this much. Ties → complex.
# Encodes asymmetric error cost (under-routing is worse than over-routing).
ROUTER_MARGIN_S = float(os.environ.get("ROUTER_MARGIN_S", "0.05"))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
WARMUP = os.environ.get("WARMUP", "1") == "1"

# Optional shared-secret auth. When set (e.g. in .env), all protected routes
# (/converse, /chat, /transcribe, /speak) require an X-API-Key header that
# matches. Generate with:  python -c "import secrets; print(secrets.token_urlsafe(32))"
# Leave empty for local-only LAN use.
API_KEY = os.environ.get("API_KEY", "").strip()

WARMUP_AUDIO = REPO_ROOT / "samples" / "jfk.wav"

PIPER_MODELS_DIR = REPO_ROOT / "models" / "piper"
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-amy-medium")
PIPER_MODEL_PATH = PIPER_MODELS_DIR / f"{PIPER_VOICE}.onnx"

# /converse caps reply length so a chatty LLM doesn't turn into 30s of audio.
# Voice replies should be short (1-2 sentences). Tune via env if needed.
CONVERSE_MAX_TOKENS = int(os.environ.get("CONVERSE_MAX_TOKENS", "96"))

# System prompt prepended to LLM calls from /converse. Targets two failure modes:
# (1) markdown formatting (asterisks, bullets) read literally by the TTS engine,
# (2) verbose replies turning into 20+ seconds of audio.
VOICE_SYSTEM_PROMPT = os.environ.get(
    "VOICE_SYSTEM_PROMPT",
    "You are a voice assistant; your reply will be read aloud by a text-to-speech "
    "engine. Reply in plain conversational prose only - no markdown (no asterisks, "
    "underscores, bullets, headers, code blocks, horizontal rules), and no LaTeX "
    "or mathematical notation. If the answer involves math, describe it in plain "
    "words (e.g. 'the derivative of natural log of x is one over x' - never "
    "'d/dx ln(x) = 1/x' or '\\frac{1}{x}'). Keep replies to 1-2 short sentences "
    "unless the question genuinely needs more detail. Make it sound natural when "
    "spoken.",
)
