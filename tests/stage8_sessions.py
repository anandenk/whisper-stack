"""Stage 8 verification: SessionStore + Session + Turn (Phase A).

No server required, no LLM call, no audio. Pure file IO + parsing.
Exercises:
  - create new session, append turns, verify file contents
  - re-open existing session within idle window, append more turns
  - recent_window slicing
  - idle rollover: with a 1-second timeout, sleep 1.5s, expect a NEW file
  - validation: stream_id with path separators rejected
  - invalid stream_id like '../etc' / 'a/b' / '.foo'
  - graceful handling of malformed turn block

Run from repo root after activating .venv:
    python tests/stage8_sessions.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.sessions import Session, SessionStore, Turn


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def _llm_result_stub(reply: str, model: str, route: str = "simple",
                     latency: float = 0.42, in_t: int = 12, out_t: int = 8):
    """Helper to keep test calls compact."""
    return dict(
        user_text=None,  # caller fills
        reply_text=reply,
        route=route,
        model=model,
        latency_s=latency,
        input_tokens=in_t,
        output_tokens=out_t,
    )


def test_create_and_append(tmp: Path) -> None:
    print("[1] create new session, append two turns")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    s = store.active_or_new("dev-test")
    must(s.path.exists(), "session file not created")
    must(s.path.parent == tmp, "session file in wrong dir")
    must(s.stream_id == "dev-test", f"stream_id mismatch: {s.stream_id!r}")
    must(s.path.name.startswith("dev-test-"), f"filename pattern wrong: {s.path.name}")
    must(s.path.name.endswith(".md"), f"extension wrong: {s.path.name}")
    must(s.recent_window(10) == [], "fresh session should have no turns")

    t1 = s.append_turn(
        user_text="What's the capital of France?",
        reply_text="The capital of France is Paris.",
        route="simple", model="llama3.1:8b-instruct-q5_K_M",
        latency_s=1.23, input_tokens=12, output_tokens=8,
    )
    must(t1.index == 1, f"first turn index should be 1, got {t1.index}")
    must(isinstance(t1, Turn), "append_turn should return a Turn")

    t2 = s.append_turn(
        user_text="And its population?",
        reply_text="About 2.1 million in the city proper.",
        route="complex", model="claude-sonnet-4-6",
        latency_s=2.07, input_tokens=47, output_tokens=18,
    )
    must(t2.index == 2, f"second turn index should be 2, got {t2.index}")

    contents = s.path.read_text(encoding="utf-8")
    must("# Session dev-test-" in contents, "missing session header")
    must("created:" in contents, "missing created header")
    must("last_turn:" in contents, "missing last_turn header")
    must("## turn 1 —" in contents, "missing turn 1 header")
    must("## turn 2 —" in contents, "missing turn 2 header")
    must("**user:** What's the capital of France?" in contents,
         "user text 1 not in file")
    must("The capital of France is Paris." in contents,
         "reply text 1 not in file")
    must("**user:** And its population?" in contents,
         "user text 2 not in file")
    must("route=complex model=claude-sonnet-4-6" in contents,
         "assistant meta line 2 not in file")

    # Re-parse from disk to make sure the round-trip is clean
    s2 = Session.load(s.path, "dev-test")
    parsed = s2.all_turns()
    must(len(parsed) == 2, f"reload should see 2 turns, got {len(parsed)}")
    must(parsed[0].user_text == "What's the capital of France?",
         f"reload user_text 1 wrong: {parsed[0].user_text!r}")
    must(parsed[0].reply_text == "The capital of France is Paris.",
         f"reload reply_text 1 wrong: {parsed[0].reply_text!r}")
    must(parsed[0].route == "simple", "reload route 1 wrong")
    must(parsed[0].model == "llama3.1:8b-instruct-q5_K_M", "reload model 1 wrong")
    must(parsed[0].input_tokens == 12, "reload in_tokens 1 wrong")
    must(parsed[0].output_tokens == 8, "reload out_tokens 1 wrong")
    must(abs(parsed[0].latency_s - 1.23) < 0.01, "reload latency 1 wrong")

    must(parsed[1].route == "complex", "reload route 2 wrong")
    must(parsed[1].model == "claude-sonnet-4-6", "reload model 2 wrong")

    print("  OK")


def test_resume_within_idle_window(tmp: Path) -> None:
    print("[2] re-open within idle window appends to same file")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    s_a = store.active_or_new("dev-resume")
    s_a.append_turn(
        user_text="hello",
        reply_text="hi",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
    )
    path_a = s_a.path

    s_b = store.active_or_new("dev-resume")
    must(s_b.path == path_a,
         f"expected same file, got {s_b.path.name} vs {path_a.name}")
    must(len(s_b.all_turns()) == 1, "loaded session should see the prior turn")

    s_b.append_turn(
        user_text="how are you",
        reply_text="fine",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
    )
    must(len(Session.load(path_a, "dev-resume").all_turns()) == 2,
         "after second append, file should have 2 turns")
    print("  OK")


def test_recent_window(tmp: Path) -> None:
    print("[3] recent_window returns only the last N turns")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    s = store.active_or_new("dev-window")
    for i in range(5):
        s.append_turn(
            user_text=f"q{i}",
            reply_text=f"a{i}",
            route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
        )
    must(len(s.recent_window(3)) == 3, "window of 3 should return 3 turns")
    must([t.user_text for t in s.recent_window(3)] == ["q2", "q3", "q4"],
         "window of 3 should be the LAST 3 turns")
    must(len(s.recent_window(99)) == 5, "window > total should return all turns")
    must(s.recent_window(0) == [], "window of 0 should be empty")
    print("  OK")


def test_idle_rollover(tmp: Path) -> None:
    print("[4] idle past timeout creates a NEW session file")
    # Sleep needs to exceed (idle_timeout + 1s) to absorb the second-resolution
    # rounding of the in-file last_turn header. With idle_timeout=1s and
    # sleep=2.5s, the worst-case observed gap is >=1.5s — comfortably past.
    store = SessionStore(tmp, idle_timeout=timedelta(seconds=1))
    s_a = store.active_or_new("dev-rollover")
    s_a.append_turn(
        user_text="first conversation",
        reply_text="ok",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
    )
    path_a = s_a.path

    time.sleep(2.5)

    s_b = store.active_or_new("dev-rollover")
    must(s_b.path != path_a,
         f"expected NEW file after idle, got same: {s_b.path.name}")
    must(len(s_b.all_turns()) == 0, "fresh session should have no turns")

    files = sorted(tmp.glob("dev-rollover-*.md"))
    must(len(files) == 2, f"expected 2 files for stream 'dev-rollover', got {len(files)}")
    print("  OK")


def test_idle_with_explicit_now(tmp: Path) -> None:
    print("[5] idle check honors caller-supplied 'now' (deterministic test)")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    t0 = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    s_a = store.active_or_new("dev-clock", now=t0)
    s_a.append_turn(
        user_text="hi", reply_text="hi back",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
        now=t0,
    )
    # 9 minutes later: still within window
    t1 = t0 + timedelta(minutes=9)
    s_b = store.active_or_new("dev-clock", now=t1)
    must(s_b.path == s_a.path, "9min later should resume same session")

    # 11 minutes later: past window
    t2 = t0 + timedelta(minutes=11)
    s_c = store.active_or_new("dev-clock", now=t2)
    must(s_c.path != s_a.path, "11min later should start new session")
    print("  OK")


def test_message_pair(tmp: Path) -> None:
    print("[6] Turn.to_message_pair() returns Anthropic/Ollama-shape dicts")
    t = Turn(
        index=1, timestamp=datetime.now(timezone.utc),
        user_text="hello", reply_text="hi there",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
    )
    pair = t.to_message_pair()
    must(pair == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ], f"pair shape wrong: {pair}")
    print("  OK")


def test_invalid_stream_id(tmp: Path) -> None:
    print("[7] stream IDs with path separators or leading dot are rejected")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    for bad in ("../etc/passwd", "a/b", "a\\b", ".hidden"):
        try:
            store.active_or_new(bad)
        except ValueError:
            continue
        must(False, f"expected ValueError for stream_id={bad!r}")
    print("  OK")


def test_malformed_block_skipped(tmp: Path) -> None:
    print("[8] malformed turn block in a file is skipped, not raised")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    s = store.active_or_new("dev-malformed")
    s.append_turn(
        user_text="good turn", reply_text="ok",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
    )

    # Hand-corrupt the file: append a `## turn 2 — TS` header but no
    # **assistant** line. The parser should drop this turn but keep the
    # good one.
    with s.path.open("a", encoding="utf-8") as f:
        f.write("## turn 2 — 2026-05-03T12:00:00Z\n")
        f.write("**user:** orphan turn with no assistant line\n\n")

    parsed = Session.load(s.path, "dev-malformed").all_turns()
    must(len(parsed) == 1, f"malformed should be dropped, got {len(parsed)} turns")
    must(parsed[0].user_text == "good turn", "wrong turn survived")
    print("  OK")


def test_prune_older_than(tmp: Path) -> None:
    print("[10] prune_older_than removes stale files; spares fresh + malformed")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    t_now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    # Three real sessions: 30d old, 5d old, fresh.
    s_old = store.active_or_new("dev-prune", now=t_now - timedelta(days=30))
    s_old.append_turn(
        user_text="ancient", reply_text="ok",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
        now=t_now - timedelta(days=30),
    )
    s_mid = store.active_or_new("dev-prune", now=t_now - timedelta(days=5))
    s_mid.append_turn(
        user_text="recent-ish", reply_text="ok",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
        now=t_now - timedelta(days=5),
    )
    s_new = store.active_or_new("dev-prune", now=t_now)
    s_new.append_turn(
        user_text="now", reply_text="ok",
        route="simple", model="m", latency_s=0.1, input_tokens=1, output_tokens=1,
        now=t_now,
    )

    # Bonus: a malformed file that has no parseable last_turn header. Should
    # be left alone.
    malformed = tmp / "dev-prune-malformed.md"
    malformed.write_text("# Session dev-prune-malformed\n(this file is corrupt)\n",
                         encoding="utf-8")

    deleted = store.prune_older_than(days=14, now=t_now)
    must(s_old.path in deleted, "30d-old session should be pruned")
    must(s_mid.path not in deleted, "5d-old session should be kept")
    must(s_new.path not in deleted, "fresh session should be kept")
    must(malformed not in deleted, "malformed file should NOT be deleted")
    must(not s_old.path.exists(), "pruned file still on disk")
    must(s_mid.path.exists() and s_new.path.exists(), "kept files vanished")
    must(malformed.exists(), "malformed file got deleted")
    print("  OK")


def test_multiline_text(tmp: Path) -> None:
    print("[9] multi-line user_text and reply_text round-trip cleanly")
    store = SessionStore(tmp, idle_timeout=timedelta(minutes=10))
    s = store.active_or_new("dev-multiline")
    s.append_turn(
        user_text="line one\nline two\nline three",
        reply_text="reply line A\nreply line B",
        route="simple", model="m", latency_s=0.1, input_tokens=3, output_tokens=2,
    )
    parsed = Session.load(s.path, "dev-multiline").all_turns()
    must(len(parsed) == 1, "expected 1 turn")
    must(parsed[0].user_text == "line one\nline two\nline three",
         f"multiline user_text round-trip broken: {parsed[0].user_text!r}")
    must(parsed[0].reply_text == "reply line A\nreply line B",
         f"multiline reply_text round-trip broken: {parsed[0].reply_text!r}")
    print("  OK")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="voice-ai-sessions-") as td:
        tmp = Path(td)
        print(f"Using tmp: {tmp}\n")
        test_create_and_append(tmp)
        test_resume_within_idle_window(tmp)
        test_recent_window(tmp)
        test_idle_rollover(tmp)
        test_idle_with_explicit_now(tmp)
        test_message_pair(tmp)
        test_invalid_stream_id(tmp)
        test_malformed_block_skipped(tmp)
        test_multiline_text(tmp)
        test_prune_older_than(tmp)

    print("\n" + "=" * 64)
    print("  All Phase A (sessions.py) checks passed.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
