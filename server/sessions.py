"""Markdown-backed multi-turn session store.

One file per active conversation at `<root>/<stream_id>-<ISO-timestamp>.md`.
Idle is checked lazily at request time — no daemon, no scheduler. Server
can be off for days; the rollover triggers on the next request.

Format: see `notes/sessions-plan.md`. Briefly:

    # Session <stream>-<timestamp>
    created: <ISO>
    last_turn: <ISO>

    ## turn 1 — <ISO>
    **user:** <transcript>
    **assistant** route=<r> model=<m> latency=<x.xs> in=<n> out=<n>
    <reply text>

    ## turn 2 — <ISO>
    ...

The `last_turn:` header line is the only thing the idle check needs to
read on every request — kept fresh by `append_turn`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Filename-safe ISO compaction — Windows won't accept ':' in filenames.
_FNAME_TS_FMT = "%Y%m%dT%H%M%SZ"
# Inside-file ISO uses the readable form so cat / grep / Obsidian render it nicely.
_INFILE_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# `[ \t]*` (NOT `\s*`) at end-of-line so the substitution doesn't gobble the
# trailing newline(s). On Windows the file ends up with CRLF, and a `\s*$`
# greedy match consumes \r and breaks up the blank-line separator.
_HEADER_LAST_TURN_RE = re.compile(r"^last_turn:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_TURN_BLOCK_RE = re.compile(
    r"^## turn (\d+) — (\S+)\s*$",  # captures (n, timestamp)
    re.MULTILINE,
)
_ASSISTANT_META_RE = re.compile(
    r"^\*\*assistant\*\*\s+"
    r"route=(?P<route>\S+)\s+"
    r"model=(?P<model>\S+)\s+"
    r"latency=(?P<latency>\S+)s\s+"
    r"in=(?P<in_tokens>\d+)\s+"
    r"out=(?P<out_tokens>\d+)\s*$",
    re.MULTILINE,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _fmt_fname_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FNAME_TS_FMT)


def _fmt_infile_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_INFILE_TS_FMT)


def _parse_infile_ts(s: str) -> datetime:
    """Tolerate the trailing 'Z' (Python <3.11 strptime won't take it)."""
    s = s.rstrip("Z")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@dataclass
class Turn:
    index: int                   # 1-based, matches the `## turn N` header
    timestamp: datetime          # UTC
    user_text: str
    reply_text: str
    route: str                   # "simple" | "complex"
    model: str
    latency_s: float
    input_tokens: int
    output_tokens: int

    def to_message_pair(self) -> list[dict]:
        """Render as the Anthropic/Ollama messages-array shape:
        [{"role": "user", ...}, {"role": "assistant", ...}].

        Phase B/C builds the LLM history list by flat-mapping this across
        the recent_window."""
        return [
            {"role": "user", "content": self.user_text},
            {"role": "assistant", "content": self.reply_text},
        ]


@dataclass
class Session:
    path: Path
    stream_id: str
    created: datetime
    last_turn: datetime
    # Cache of parsed turns; populated on first read.
    _turns: list[Turn] | None = field(default=None, repr=False)

    # ---- factory ----

    @classmethod
    def create(cls, root: Path, stream_id: str, now: datetime) -> Session:
        """Create a brand-new session file. No turns yet."""
        fname = f"{stream_id}-{_fmt_fname_ts(now)}.md"
        path = root / fname
        ts = _fmt_infile_ts(now)
        path.write_text(
            f"# Session {stream_id}-{_fmt_fname_ts(now)}\n"
            f"created: {ts}\n"
            f"last_turn: {ts}\n\n",
            encoding="utf-8",
        )
        return cls(path=path, stream_id=stream_id, created=now, last_turn=now, _turns=[])

    @classmethod
    def load(cls, path: Path, stream_id: str) -> Session:
        """Load an existing session file. Defers turn parsing until needed."""
        text = path.read_text(encoding="utf-8")
        created_match = re.search(r"^created:\s*(\S+)\s*$", text, re.MULTILINE)
        last_match = _HEADER_LAST_TURN_RE.search(text)
        if not created_match or not last_match:
            raise ValueError(f"session file missing created/last_turn header: {path}")
        return cls(
            path=path,
            stream_id=stream_id,
            created=_parse_infile_ts(created_match.group(1)),
            last_turn=_parse_infile_ts(last_match.group(1)),
        )

    # ---- read ----

    def all_turns(self) -> list[Turn]:
        if self._turns is None:
            self._turns = _parse_turns(self.path.read_text(encoding="utf-8"))
        return self._turns

    def recent_window(self, n: int) -> list[Turn]:
        if n <= 0:
            return []
        turns = self.all_turns()
        return turns[-n:]

    # ---- write ----

    def append_turn(
        self,
        user_text: str,
        reply_text: str,
        *,
        route: str,
        model: str,
        latency_s: float,
        input_tokens: int,
        output_tokens: int,
        now: datetime | None = None,
    ) -> Turn:
        """Append a turn and bump the last_turn header. Returns the Turn record."""
        now = now or _utcnow()
        next_index = len(self.all_turns()) + 1
        turn = Turn(
            index=next_index,
            timestamp=now,
            user_text=user_text,
            reply_text=reply_text,
            route=route,
            model=model,
            latency_s=latency_s,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        block = (
            f"## turn {turn.index} — {_fmt_infile_ts(now)}\n"
            f"**user:** {user_text}\n"
            f"**assistant** route={route} model={model} "
            f"latency={latency_s:.2f}s in={input_tokens} out={output_tokens}\n"
            f"{reply_text}\n\n"
        )

        # Read full file, append the new block, rewrite the last_turn header.
        # Single-user single-process; no concurrency to guard against.
        text = self.path.read_text(encoding="utf-8")
        text = _HEADER_LAST_TURN_RE.sub(
            f"last_turn: {_fmt_infile_ts(now)}", text, count=1
        )
        # Ensure the file ends in a blank line before appending the new block.
        if not text.endswith("\n\n"):
            text = text.rstrip("\n") + "\n\n"
        text += block
        self.path.write_text(text, encoding="utf-8")

        self.last_turn = now
        # Keep the in-memory cache consistent.
        if self._turns is not None:
            self._turns.append(turn)
        return turn


def _parse_turns(text: str) -> list[Turn]:
    """Walk the file's `## turn` blocks and pull each into a Turn dataclass.

    Tolerant: ignores anything before the first `## turn` header, allows
    multi-line user_text and reply_text, doesn't try to validate strict
    field order beyond what the regex demands.
    """
    headers = list(_TURN_BLOCK_RE.finditer(text))
    if not headers:
        return []

    turns: list[Turn] = []
    for i, hdr in enumerate(headers):
        idx = int(hdr.group(1))
        ts = _parse_infile_ts(hdr.group(2))
        body_start = hdr.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end].strip("\n")

        # Find user line + assistant meta line; everything between user and
        # assistant is user_text, everything after assistant meta is reply.
        user_marker = "**user:**"
        assistant_match = _ASSISTANT_META_RE.search(body)
        if user_marker not in body or not assistant_match:
            # Skip malformed blocks rather than crash — sessions are append-
            # only, a broken turn shouldn't take down the whole file.
            continue

        user_start = body.index(user_marker) + len(user_marker)
        user_end = assistant_match.start()
        user_text = body[user_start:user_end].strip()

        reply_text = body[assistant_match.end():].strip("\n")

        turns.append(Turn(
            index=idx,
            timestamp=ts,
            user_text=user_text,
            reply_text=reply_text,
            route=assistant_match.group("route"),
            model=assistant_match.group("model"),
            latency_s=float(assistant_match.group("latency")),
            input_tokens=int(assistant_match.group("in_tokens")),
            output_tokens=int(assistant_match.group("out_tokens")),
        ))

    return turns


class SessionStore:
    """Filesystem-backed session manager. One instance per server process."""

    def __init__(self, root: Path | str, idle_timeout: timedelta) -> None:
        self.root = Path(root)
        self.idle_timeout = idle_timeout
        self.root.mkdir(parents=True, exist_ok=True)

    def active_or_new(self, stream_id: str, now: datetime | None = None) -> Session:
        """Return the active session for this stream, rolling over if idle.

        Lookup is a glob over `<root>/<stream_id>-*.md`, sorted lexically
        (the ISO timestamp suffix sorts chronologically). Idle check reads
        only the `last_turn:` header line of the most recent file.
        """
        if "/" in stream_id or "\\" in stream_id or stream_id.startswith("."):
            # Defense against stream IDs containing path separators or dotfiles.
            # The Phase C/D handlers will validate format before reaching here,
            # but we also belt-and-suspender at the store boundary.
            raise ValueError(f"invalid stream_id: {stream_id!r}")

        now = now or _utcnow()
        candidates = sorted(self.root.glob(f"{stream_id}-*.md"))
        if candidates:
            latest = candidates[-1]
            last_turn = _peek_last_turn(latest)
            if last_turn is not None and (now - last_turn) <= self.idle_timeout:
                return Session.load(latest, stream_id)
        # No candidates, or the latest one is past the idle cliff.
        return Session.create(self.root, stream_id, now)

    def list_sessions(self, stream_id: str) -> list[Path]:
        """Sorted oldest -> newest. Cheap; just a glob + sort."""
        return sorted(self.root.glob(f"{stream_id}-*.md"))


def _peek_last_turn(path: Path) -> datetime | None:
    """Read just the file header to grab the last_turn timestamp.

    Reads the first ~1KB; the header is always within the first few lines.
    Returns None on parse failure so caller can fall back to creating a
    fresh session rather than crash.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            head = f.read(1024)
    except OSError:
        return None
    m = _HEADER_LAST_TURN_RE.search(head)
    if not m:
        return None
    try:
        return _parse_infile_ts(m.group(1))
    except ValueError:
        return None
