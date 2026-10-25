"""Local token/cost records, without prompts, transcripts, replies, or secrets."""

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3


PRICING_DATE = "2026-10-25"
PRICING_SOURCE = "https://platform.claude.com/docs/en/models/haiku-5-5/overview"
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def usage_estimate(usage):
    """Standard/global Haiku rates; thinking is already in output_tokens."""
    if not isinstance(usage, dict):
        return None, None
    tokens = {name: usage.get(name, 0) for name in TOKEN_FIELDS}
    if "input_tokens" not in usage or "output_tokens" not in usage or any(
        type(value) is not int or value < 0 for value in tokens.values()
    ):
        return None, None
    details = usage.get("output_tokens_details") or {}
    thinking = details.get("thinking_tokens") if isinstance(details, dict) else None
    if thinking is not None and (type(thinking) is not int or not 0 <= thinking <= tokens["output_tokens"]):
        return None, None
    tokens["thinking_tokens"] = thinking
    cache = usage.get("cache_creation") or {}
    if not isinstance(cache, dict):
        return tokens, None
    hour = cache.get("ephemeral_1h_input_tokens", 0)
    five = cache.get("ephemeral_5m_input_tokens", tokens["cache_creation_input_tokens"] - hour) if type(hour) is int else -1
    if any(type(value) is not int or value < 0 for value in (hour, five)) or hour + five != tokens["cache_creation_input_tokens"]:
        return tokens, None
    if (usage.get("inference_geo", "global") != "global" or usage.get("speed", "standard") != "standard"
            or usage.get("service_tier", "standard") != "standard"):
        return tokens, None
    total_input = tokens["input_tokens"] + tokens["cache_creation_input_tokens"] + tokens["cache_read_input_tokens"]
    multiplier = 5 if total_input > 100_000 else 1
    cost = (
        Decimal(tokens["input_tokens"]) * Decimal("0.10")
        + Decimal(tokens["output_tokens"]) * Decimal("0.50")
        + Decimal(tokens["cache_read_input_tokens"]) * Decimal("0.01")
        + Decimal(five) * Decimal("0.125") + Decimal(hour) * Decimal("0.20")
    ) * multiplier / 1_000_000
    return tokens, format(cost, ".9f")


class Diagnostics:
    """One SQLite row per attempted Haiku request; survives server restarts."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)")

    def save(self, event):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO requests VALUES (?, ?, ?)", (event["id"], event["created_at"], json.dumps(event)))

    def get(self, identifier):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT data FROM requests WHERE id = ?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def summary(self):
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        def empty():
            return {"llm_requests": 0, "successful_replies": 0, "requests_without_cost_estimate": 0,
                    **{field: 0 for field in TOKEN_FIELDS}, "estimated_cost_usd": Decimal(0)}

        lifetime, monthly = empty(), empty()
        with closing(sqlite3.connect(self.path)) as db:
            for created, data in db.execute("SELECT created_at, data FROM requests"):
                event = json.loads(data)
                for totals in (lifetime, monthly) if created.startswith(month) else (lifetime,):
                    totals["llm_requests"] += 1
                    totals["successful_replies"] += event["status"] == "ok"
                    cost = event.get("estimated_cost_usd")
                    totals["requests_without_cost_estimate"] += cost is None
                    if cost is not None:
                        totals["estimated_cost_usd"] += Decimal(cost)
                    tokens = event.get("tokens") or {}
                    for name in TOKEN_FIELDS:
                        totals[name] += tokens.get(name, 0)
            recent = [json.loads(row[0]) for row in db.execute("SELECT data FROM requests ORDER BY created_at DESC LIMIT 20")]
        for totals in (lifetime, monthly):
            totals["estimated_cost_usd"] = format(totals["estimated_cost_usd"], ".9f")
            totals["cost_estimate_complete"] = totals["requests_without_cost_estimate"] == 0
        return {"scope": "Haiku requests through this backend only; excludes ElevenLabs and other account usage",
                "currency": "USD", "cost_is_estimate": True, "pricing_checked": PRICING_DATE,
                "pricing_source": PRICING_SOURCE, "month_utc": month,
                "lifetime": lifetime, "this_month": monthly, "recent": recent}
