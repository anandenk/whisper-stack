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

# Selects the backend set instantiated at process boot. One process serves
# one mode — no runtime switching. Validated here so a typo fails fast.
MODE = os.environ.get("MODE", "local").strip().lower()
if MODE not in ("local", "cloud"):
    raise SystemExit(f"MODE must be 'local' or 'cloud', got {MODE!r}")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")
# Pin STT language. Empty = auto-detect (faster-whisper's default — known to
# misfire into Icelandic / Welsh / etc on short or noisy utterances). Set to
# the BCP-47 lang code you actually speak ("en", "es", "fr", ...) to skip
# detection entirely.
_whisper_lang_raw = os.environ.get("WHISPER_LANGUAGE", "en").strip()
WHISPER_LANGUAGE: str | None = _whisper_lang_raw if _whisper_lang_raw else None

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# Cloud-mode simple-route model. The bare alias may need to be the dated form
# (e.g. claude-haiku-4-5-20251001); Phase 6 verifies and updates if needed.
ANTHROPIC_HAIKU_MODEL = os.environ.get("ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5")

# [cloud] STT — Deepgram nova-3
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "").strip()

# [cloud] TTS — OpenAI tts-1 (PCM)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")

# Use 127.0.0.1 not 'localhost' on Windows — DNS/IPv6 fallback adds ~2s per request.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
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
# Speech rate. Lower = faster. Default 0.85 is ~15% faster than 1.0; still
# natural. Voice-agent UX feels noticeably snappier with this set.
# Below ~0.75 starts to sound rushed; above ~1.1 starts to sound sleepy.
PIPER_LENGTH_SCALE = float(os.environ.get("PIPER_LENGTH_SCALE", "0.85"))

# /converse caps reply length so a chatty LLM doesn't turn into 30s of audio.
# Voice replies should be short (1-2 sentences). Tune via env if needed.
CONVERSE_MAX_TOKENS = int(os.environ.get("CONVERSE_MAX_TOKENS", "96"))

# Tool use safety cap — max iterations of the model -> tool -> model loop
# inside one chat() call. Prevents a runaway "model keeps calling tools" bug
# from billing forever. Each round is one Anthropic / Ollama call.
TOOL_MAX_ROUNDS = int(os.environ.get("TOOL_MAX_ROUNDS", "5"))

# [both] web search via Tavily (https://tavily.com). Free tier covers 1k
# queries/mo. When unset, the web_search tool is NOT registered — the model
# simply doesn't see it as an option, so it can't try and fail.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# Multi-turn session memory (see notes/sessions-plan.md).
# - SESSION_DIR is REPO_ROOT-relative unless an absolute path is given.
#   On a Fly.io VM this should point at a mounted volume so redeploys don't wipe history.
# - Idle timeout: gap between requests beyond which a new session file rolls over.
#   Lazy check at request time — no daemon, no scheduler.
# - Window: max recent turns sent to the LLM as `history`. Idle is the real
#   conversation boundary; the window is a safety belt against runaway exchanges.
_session_dir_raw = os.environ.get("SESSION_DIR", "sessions").strip()
_session_dir_path = Path(_session_dir_raw)
SESSION_DIR = _session_dir_path if _session_dir_path.is_absolute() else (REPO_ROOT / _session_dir_path)
SESSION_IDLE_TIMEOUT_MIN = float(os.environ.get("SESSION_IDLE_TIMEOUT_MIN", "10"))
SESSION_WINDOW_TURNS = int(os.environ.get("SESSION_WINDOW_TURNS", "30"))
# Optional TTL: if set, sessions older than N days are deleted at server boot.
# Unset / empty = never prune. Disk cost is trivial for one user, but on a
# Fly.io volume (cloud mode) this caps growth without a daemon.
_session_ttl_raw = os.environ.get("SESSION_TTL_DAYS", "").strip()
SESSION_TTL_DAYS: float | None = float(_session_ttl_raw) if _session_ttl_raw else None

# Wake-word client (tests/jarvis.py). Used only when running that client;
# the Flask server itself doesn't read these. Pulling them through config.py
# keeps the .env layout consistent with other knobs.
WAKEWORD_MODEL = os.environ.get("WAKEWORD_MODEL", "hey_jarvis")
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))
# Optional chime played on wake-word detection. Empty / non-existent path
# falls back to an in-process synthetic beep so the user always gets a
# confirmation sound. REPO_ROOT-relative unless absolute.
_wakeword_chime_raw = os.environ.get("WAKEWORD_CHIME", "samples/chime.wav").strip()
_wakeword_chime_path = Path(_wakeword_chime_raw)
WAKEWORD_CHIME: Path = (
    _wakeword_chime_path if _wakeword_chime_path.is_absolute()
    else (REPO_ROOT / _wakeword_chime_path)
)
# After the reply finishes, jarvis.py optionally stays in continuous mode
# for this long (no further wake word required) before returning to
# passive wake-word listening. Phase 6B uses this; 6A always returns
# immediately. Tunable so households with chatty backgrounds can dial it
# down.
WAKEWORD_CONVERSATION_TIMEOUT_S = float(
    os.environ.get("WAKEWORD_CONVERSATION_TIMEOUT_S", "30")
)

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
