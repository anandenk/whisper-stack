"""Markdown / LaTeX -> speakable plain text.

Mode-independent: lives outside tts.py so cloud-mode TTS backends can reuse
the same stripper without pulling in Piper.

LLMs slip in markdown even when told not to; TTS engines read punctuation
literally ("**2**" becomes "asterisk asterisk 2 asterisk asterisk"). Strip
before synth.
"""

from __future__ import annotations

import re

_RE_TRIPLE_BACKTICK = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_RE_LATEX_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_RE_LATEX_INLINE = re.compile(r"\$[^$\n]+?\$")
_RE_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+\*?(?:\{[^}]*\})*")
_RE_BOLD_ASTERISK = re.compile(r"\*\*\*([^\*\n]+)\*\*\*|\*\*([^\*\n]+)\*\*")
_RE_BOLD_UNDERSCORE = re.compile(r"___([^_\n]+)___|__([^_\n]+)__")
_RE_ITALIC_ASTERISK = re.compile(r"\*([^\*\n]+)\*")
_RE_ITALIC_UNDERSCORE = re.compile(r"\b_([^_\n]+)_\b")
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_HEADER = re.compile(r"^#+\s+", re.MULTILINE)
_RE_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_RE_HORIZONTAL_RULE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_RE_BLANK_LINES = re.compile(r"\n\s*\n+")
_RE_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")


def to_speakable_text(text: str) -> str:
    """Strip markdown / LaTeX so the TTS engine reads prose, not punctuation.

    Order matters: drop block-level constructs (code fences, math blocks) before
    inline ones, and strip LaTeX commands BEFORE bold/italic so a stray backslash
    sequence inside math doesn't survive.
    """
    text = _RE_TRIPLE_BACKTICK.sub(lambda m: m.group(1) or "", text)
    text = _RE_INLINE_CODE.sub(r"\1", text)
    text = _RE_LATEX_BLOCK.sub(" ", text)
    text = _RE_LATEX_INLINE.sub(" ", text)
    text = _RE_LATEX_COMMAND.sub(" ", text)
    text = _RE_BOLD_ASTERISK.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _RE_BOLD_UNDERSCORE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _RE_ITALIC_ASTERISK.sub(r"\1", text)
    text = _RE_ITALIC_UNDERSCORE.sub(r"\1", text)
    text = _RE_LINK.sub(r"\1", text)
    text = _RE_HEADER.sub("", text)
    text = _RE_BULLET.sub("", text)
    text = _RE_HORIZONTAL_RULE.sub("", text)
    text = _RE_BLANK_LINES.sub("\n", text)
    text = _RE_RUN_OF_SPACES.sub(" ", text)
    return text.strip()
