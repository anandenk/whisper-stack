"""get_current_time — server-side time lookup.

Free, instant, no external dependencies. The most useful "are tools wired
up at all?" smoke test, plus actually useful for "what time is it in Tokyo"
queries.

Uses Python's stdlib `zoneinfo` (3.9+). On Windows, zoneinfo falls back to
the `tzdata` package if the OS doesn't ship IANA zone files; our venv
already has tzdata via downstream deps, but we handle the missing-tz case
gracefully either way.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server.tools import register_tool
from server.tools.base import Tool


def _handler(args: dict) -> str:
    tz_name = (args.get("timezone") or "").strip()
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            return (f"Unknown timezone {tz_name!r}. "
                    "Try an IANA name like 'America/Chicago' or 'Europe/London'.")
        now = datetime.now(tz)
    else:
        now = datetime.now().astimezone()
    # Strip any trailing zero-padding artifacts that vary by platform.
    return now.strftime("%A, %B %d, %Y at %I:%M %p %Z").strip()


get_current_time = Tool(
    name="get_current_time",
    description=(
        "Get the current date and time. Optionally specify an IANA timezone "
        "like 'America/Chicago' or 'Asia/Tokyo'. Without a timezone, returns "
        "the server's local time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "Optional IANA timezone identifier (e.g. 'America/New_York', "
                    "'Europe/London', 'Asia/Tokyo'). Omit for the server's local time."
                ),
            },
        },
        "required": [],
    },
    handler=_handler,
)

register_tool(get_current_time)
