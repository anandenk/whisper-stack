"""Centralized config + .env loading.

Imported first by every other server module. `load_dotenv(override=True)` is
mandatory: an empty GOOGLE_API_KEY can exist in the user's Windows env vars,
and without override the real key in .env is silently ignored.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)

# ---- STT (faster-whisper, local GPU) ----------------------------------------
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")
# Pin STT language. Empty = auto-detect (faster-whisper's default — known to
# misfire into Icelandic / Welsh / etc on short or noisy utterances). Set to
# the BCP-47 lang code you actually speak ("en", "es", "fr", ...) to skip
# detection entirely.
_whisper_lang_raw = os.environ.get("WHISPER_LANGUAGE", "en").strip()
WHISPER_LANGUAGE: str | None = _whisper_lang_raw if _whisper_lang_raw else None

# ---- LLM (Google Gemini) ----------------------------------------------------
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# ---- TTS (Piper, local) -----------------------------------------------------
PIPER_MODELS_DIR = REPO_ROOT / "models" / "piper"
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-amy-medium")
PIPER_MODEL_PATH = PIPER_MODELS_DIR / f"{PIPER_VOICE}.onnx"
# Speech rate. Lower = faster. Default 0.85 is ~15% faster than 1.0; still
# natural. Voice-agent UX feels noticeably snappier with this set.
# Below ~0.75 starts to sound rushed; above ~1.1 starts to sound sleepy.
PIPER_LENGTH_SCALE = float(os.environ.get("PIPER_LENGTH_SCALE", "0.85"))
# Optional substring matched against output device names. When set, overrides
# the system default output for /speak and /converse playback so a Bluetooth
# headphone plugging in (or another app changing the default) doesn't reroute
# the assistant's voice unexpectedly. Empty = use whatever the system default
# is at the moment of playback. Avoid Hands-Free Bluetooth profiles
# ('Headset (...)' entries) — they run at 8 kHz and TTS sounds awful through
# them; pin A2DP/stereo or a wired/USB device.
TTS_OUTPUT_DEVICE = os.environ.get("TTS_OUTPUT_DEVICE", "").strip()

# ---- HTTP server ------------------------------------------------------------
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
WARMUP = os.environ.get("WARMUP", "1") == "1"

# Optional shared-secret auth. When set (e.g. in .env), all protected routes
# (/converse, /chat, /transcribe, /speak, /listen) require an X-API-Key header
# that matches. Generate with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
# Leave empty for local-only LAN use.
API_KEY = os.environ.get("API_KEY", "").strip()

WARMUP_AUDIO = REPO_ROOT / "samples" / "jfk.wav"

# /converse caps reply length so a runaway prompt can't generate 30s of audio.
# We aim to constrain length via the system prompt (see VOICE_SYSTEM_PROMPT)
# rather than hard-truncating, so this is a safety net, not the primary limit.
# 256 leaves comfortable room for a well-behaved 1-2 sentence reply with
# grounding citations.
CONVERSE_MAX_TOKENS = int(os.environ.get("CONVERSE_MAX_TOKENS", "256"))

# Tool use safety cap — max iterations of the model -> tool -> model loop
# inside one chat() call. Prevents a runaway "model keeps calling tools" bug
# from billing forever. Each round is one Gemini call.
TOOL_MAX_ROUNDS = int(os.environ.get("TOOL_MAX_ROUNDS", "5"))

# ---- Sessions (multi-turn memory; see notes/sessions-plan.md) ---------------
# - SESSION_DIR is REPO_ROOT-relative unless an absolute path is given.
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
# Unset / empty = never prune.
_session_ttl_raw = os.environ.get("SESSION_TTL_DAYS", "").strip()
SESSION_TTL_DAYS: float | None = float(_session_ttl_raw) if _session_ttl_raw else None

# ---- Wake-word client (tests/jarvis.py) -------------------------------------
# Used only when running that client; the Flask server itself doesn't read these.
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
# passive wake-word listening.
WAKEWORD_CONVERSATION_TIMEOUT_S = float(
    os.environ.get("WAKEWORD_CONVERSATION_TIMEOUT_S", "30")
)

# Loopback-test toggle: when "1", every /converse upload is ALSO saved as
# samples/_loopback_turn_<unix-ts>.wav (post-normalize) and
# samples/_loopback_turn_<unix-ts>.bin (raw pre-normalize, audio/l16 path
# only) so an offline analyzer can replay every turn. Default off — only
# enable while running tools/loopback_test.py.
LOOPBACK_KEEP_PER_TURN = os.environ.get("LOOPBACK_KEEP_PER_TURN", "0") == "1"

# System prompt prepended to LLM calls from /converse. Voice replies should
# be tight: one or two sentences, no formatting, no padding. Earlier
# versions said "1-2 sentences unless the question really needs more
# detail" which the model read as permission to dump a 6-sentence
# weather forecast with bulleted day-by-day breakdowns. The rules below
# are intentionally absolute and ban the specific failure modes we saw:
# markdown bullets that the TTS reads literally as the word "asterisk",
# parenthetical unit conversions ("78°F (26°C)") that double the audio
# length, and "would you like me to..." filler.
VOICE_SYSTEM_PROMPT = os.environ.get(
    "VOICE_SYSTEM_PROMPT",
    "You are a voice assistant. Your reply is read aloud immediately by a "
    "text-to-speech engine.\n\n"
    "Default to short replies — one or two sentences. Expand only when "
    "the question genuinely needs depth (explaining a concept, walking "
    "through steps, telling a story). When you expand, stay "
    "conversational: complete sentences in paragraph form, never bullet "
    "lists or multi-period breakdowns.\n\n"
    "Always:\n"
    "- Pick one unit, never both. Say 'seventy-nine degrees' not "
    "'79°F (26°C)'.\n"
    "- Skip filler ('Sure!', 'Great question', 'Let me explain') and "
    "follow-up offers ('Would you like me to...').\n"
    "- Use no markdown (asterisks, underscores, hyphens-as-bullets, "
    "headers, code blocks) and no LaTeX. Describe math in plain words.\n\n"
    "Get to the answer immediately. Make it sound natural when spoken.",
)
