"""Markdown-backed multi-turn session store.

One file per active conversation at `<root>/<stream_id>-<ISO-timestamp>.md`,
optionally with an auto-titled slug suffix: `<stream>-<ISO>-<slug>.md`.
Idle is checked lazily at request time — no daemon, no scheduler. Server
can be off for days; the rollover triggers on the next request.

Format: see `notes/sessions-plan.md`. Briefly:

    # Session <stream>-<timestamp>
    title: <slug>                 (optional, set after first turn by titler)
    created: <ISO>
    last_turn: <ISO>

    ## turn 1 — <ISO>
    **user:** <transcript>
    **assistant** model=<m> latency=<x.xs> in=<n> out=<n>
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
_HEADER_TITLE_RE = re.compile(r"^title:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_HEADER_SESSION_LINE_RE = re.compile(r"^# Session .*$", re.MULTILINE)
# Slug max length keeps full filename comfortably under common 255-byte filesystem
# limits even with long stream_ids; also makes `ls sessions/` readable.
_SLUG_MAX_LEN = 30
_TURN_BLOCK_RE = re.compile(
    r"^## turn (\d+) — (\S+)\s*$",  # captures (n, timestamp)
    re.MULTILINE,
)
_ASSISTANT_META_RE = re.compile(
    r"^\*\*assistant\*\*\s+"
    r"(?:route=\S+\s+)?"  # tolerated for old session files; no longer written
    r"model=(?P<model>\S+)\s+"
    r"latency=(?P<latency>\S+)s\s+"
    r"in=(?P<in_tokens>\d+)\s+"
    r"out=(?P<out_tokens>\d+)"
    r"[^\n]*$",  # any trailing fields (legacy 'escalated=true') are ignored
    re.MULTILINE,
)
# `**tools:** name(k=v, ...); name2(...)` — optional one-liner appended after
# the reply text when the turn used any tools. Used for human-grep + future
# session inspection UIs; NOT replayed to the model on history reload.
_TOOLS_LINE_RE = re.compile(r"^\*\*tools:\*\*\s*(?P<calls>[^\n]+)$", re.MULTILINE)


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


def _parse_tools_line(s: str) -> list[dict]:
    """Inverse of _format_tools_line. Lossy but tolerant — recovers
    [{"name": str, "input": dict[str, str]}] from
    `name(k=v, k=v); name2(k=v)`. Args are returned as strings (no type
    coercion); callers that need typed args should reach for the
    structured `tools_used` from the live response, not the file.
    """
    out: list[dict] = []
    for chunk in (s or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Try to peel off `name(args)` shape.
        name = chunk
        args_str = ""
        if "(" in chunk and chunk.endswith(")"):
            name, _, rest = chunk.partition("(")
            args_str = rest[:-1]
            name = name.strip()
        args: dict[str, str] = {}
        for kv in args_str.split(","):
            kv = kv.strip()
            if not kv or "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            args[k.strip()] = v.strip()
        out.append({"name": name, "input": args})
    return out


def _format_tools_line(tools_used: list[dict]) -> str:
    """Render `[{name, input, ...}]` as 'name(k=v, k=v); name2(...)'.

    Plain text by design — easy to grep, no quotes/escapes. Voice-style
    tool inputs rarely contain `=` or `;`; if they ever do, this format
    is lossy on round-trip. Acceptable for an observability-only field.
    """
    parts: list[str] = []
    for call in tools_used:
        name = str(call.get("name") or "?")
        inp = call.get("input") or {}
        if isinstance(inp, dict) and inp:
            args = ", ".join(f"{k}={v}" for k, v in inp.items())
            parts.append(f"{name}({args})")
        else:
            parts.append(f"{name}()")
    return "; ".join(parts)


def _slugify(text: str, max_len: int = _SLUG_MAX_LEN) -> str:
    """Lowercase ASCII slug for use in filenames and the in-file title header.

    Empty input or input with no usable characters returns "" — caller should
    treat that as 'no title' and skip the rename rather than producing an
    empty-suffix filename like `<stream>-<ts>-.md`.
    """
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


@dataclass
class Turn:
    index: int                   # 1-based, matches the `## turn N` header
    timestamp: datetime          # UTC
    user_text: str
    reply_text: str
    model: str
    latency_s: float
    input_tokens: int
    output_tokens: int
    # Compact summary of tool calls made during this turn (for observability;
    # NOT replayed to the model). Each entry: {"name": str, "input": dict}.
    tools_used: list[dict] | None = None

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
    title: str | None = None
    # Cache of parsed turns; populated on first read.
    _turns: list[Turn] | None = field(default=None, repr=False)

    # ---- factory ----

    @classmethod
    def create(cls, root: Path, stream_id: str, now: datetime) -> Session:
        """Create a brand-new session file. No turns yet, no title."""
        fname = f"{stream_id}-{_fmt_fname_ts(now)}.md"
        path = root / fname
        ts = _fmt_infile_ts(now)
        path.write_text(
            f"# Session {stream_id}-{_fmt_fname_ts(now)}\n"
            f"created: {ts}\n"
            f"last_turn: {ts}\n\n",
            encoding="utf-8",
        )
        return cls(path=path, stream_id=stream_id, created=now, last_turn=now,
                   title=None, _turns=[])

    @classmethod
    def load(cls, path: Path, stream_id: str) -> Session:
        """Load an existing session file. Defers turn parsing until needed."""
        text = path.read_text(encoding="utf-8")
        created_match = re.search(r"^created:[ \t]*(\S+)[ \t]*$", text, re.MULTILINE)
        last_match = _HEADER_LAST_TURN_RE.search(text)
        if not created_match or not last_match:
            raise ValueError(f"session file missing created/last_turn header: {path}")
        title_match = _HEADER_TITLE_RE.search(text)
        return cls(
            path=path,
            stream_id=stream_id,
            created=_parse_infile_ts(created_match.group(1)),
            last_turn=_parse_infile_ts(last_match.group(1)),
            title=title_match.group(1) if title_match else None,
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

    def set_title(self, title: str) -> bool:
        """Insert/replace the `title:` header line and rename the file to add
        the slug suffix. Returns True if the title was set, False if `title`
        slugifies to empty (no rename, no header change).

        Idempotent for the no-change case: re-setting the same title just
        rewrites the header line with the same slug; the rename is a no-op.
        """
        slug = _slugify(title)
        if not slug:
            return False

        text = self.path.read_text(encoding="utf-8")
        if _HEADER_TITLE_RE.search(text):
            text = _HEADER_TITLE_RE.sub(f"title: {slug}", text, count=1)
        else:
            # Insert a `title:` line right after the `# Session ...` header line.
            text = _HEADER_SESSION_LINE_RE.sub(
                lambda m: f"{m.group(0)}\ntitle: {slug}", text, count=1
            )
        self.path.write_text(text, encoding="utf-8")

        # Rename the file: <stream>-<ts>(-<oldslug>)?.md -> <stream>-<ts>-<slug>.md.
        # The timestamp prefix stays sortable; the slug is just a suffix.
        old = self.path
        ts_part = _fmt_fname_ts(self.created)
        new_name = f"{self.stream_id}-{ts_part}-{slug}.md"
        new_path = old.with_name(new_name)
        if new_path != old:
            try:
                old.replace(new_path)
                self.path = new_path
            except OSError:
                # Rename failure is non-fatal — title-in-header still landed,
                # just not reflected in the filename. Log-worthy if we had logging.
                pass
        self.title = slug
        return True

    def append_turn(
        self,
        user_text: str,
        reply_text: str,
        *,
        model: str,
        latency_s: float,
        input_tokens: int,
        output_tokens: int,
        tools_used: list[dict] | None = None,
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
            model=model,
            latency_s=latency_s,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tools_used=tools_used,
        )

        tools_line = ""
        if tools_used:
            tools_line = f"**tools:** {_format_tools_line(tools_used)}\n"
        block = (
            f"## turn {turn.index} — {_fmt_infile_ts(now)}\n"
            f"**user:** {user_text}\n"
            f"**assistant** model={model} "
            f"latency={latency_s:.2f}s in={input_tokens} out={output_tokens}\n"
            f"{reply_text}\n"
            f"{tools_line}\n"
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

        reply_chunk = body[assistant_match.end():]

        # Pull out the optional `**tools:**` line. It always lives after the
        # reply text; strip it before storing reply_text so it doesn't end up
        # being read back as part of the model's response.
        tools_used: list[dict] | None = None
        tools_match = _TOOLS_LINE_RE.search(reply_chunk)
        if tools_match:
            tools_used = _parse_tools_line(tools_match.group("calls"))
            reply_chunk = (
                reply_chunk[:tools_match.start()] + reply_chunk[tools_match.end():]
            )

        reply_text = reply_chunk.strip("\n").strip()

        turns.append(Turn(
            index=idx,
            timestamp=ts,
            user_text=user_text,
            reply_text=reply_text,
            model=assistant_match.group("model"),
            latency_s=float(assistant_match.group("latency")),
            input_tokens=int(assistant_match.group("in_tokens")),
            output_tokens=int(assistant_match.group("out_tokens")),
            tools_used=tools_used,
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

    def prune_older_than(self, days: float, now: datetime | None = None) -> list[Path]:
        """Delete every session file whose last_turn is older than `days`.

        Uses the in-file `last_turn:` header, NOT mtime — matches the idle
        check's source-of-truth so backups/rsync/antivirus touching mtime
        can't rescue (or condemn) sessions. Files with unreadable or
        missing headers are skipped, never deleted; an unparseable file is
        more useful as a forensic record than as dropped state.

        Returns the list of paths that were deleted.
        """
        if days < 0:
            raise ValueError(f"days must be >= 0, got {days}")
        now = now or _utcnow()
        threshold = now - timedelta(days=days)
        deleted: list[Path] = []
        for path in self.root.glob("*.md"):
            last_turn = _peek_last_turn(path)
            if last_turn is None:
                continue
            if last_turn < threshold:
                try:
                    path.unlink()
                    deleted.append(path)
                except OSError:
                    # Best-effort cleanup — a permission error on one file
                    # shouldn't take down the rest of the prune pass.
                    continue
        return deleted


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
