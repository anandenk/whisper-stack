"""Auto-titler for sessions.

One Gemini call after the first turn of a new session lands. Generates a
3-5 word title; the slug derived from it goes into the file header +
filename. Failures are swallowed by the caller — a title is cosmetic and
a transient API error shouldn't 500 the user's first conversation turn.
"""

from __future__ import annotations

from server.backends.llm_gemini import GeminiLLM

_TITLE_SYSTEM_PROMPT = (
    "You generate short, descriptive titles for conversations. "
    "Given a single user message and the assistant's reply, respond with a "
    "3-to-5-word title summarizing the topic. "
    "Reply with ONLY the title — no quotes, no punctuation, no explanation, "
    "no labels like 'Title:'. Lowercase or mixed case is fine."
)

_titler: GeminiLLM | None = None


def _get_titler() -> GeminiLLM:
    global _titler
    if _titler is None:
        _titler = GeminiLLM()
    return _titler


def title_for_turn(user_text: str, reply_text: str, max_chars: int = 80) -> str:
    """Returns a short title string for the (user, reply) pair, or "" on failure.

    Keeps the prompt tiny — only the first ~80 chars of each side. Titles
    are about topic gist; a long reply doesn't need to be fully consumed.
    """
    snippet = (
        f"User: {user_text[:max_chars]}\n"
        f"Assistant: {reply_text[:max_chars]}"
    )
    try:
        d = _get_titler().chat(
            snippet,
            max_tokens=24,
            system_prompt=_TITLE_SYSTEM_PROMPT,
        )
    except Exception:
        # Network blip, 400, anything — title is cosmetic. Swallow.
        return ""
    title = (d.get("reply") or "").strip()
    # Strip wrapping quotes the model sometimes adds despite instructions.
    title = title.strip('"').strip("'").strip()
    return title
