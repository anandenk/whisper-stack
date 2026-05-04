"""Tool dataclass + Anthropic/Ollama schema converters.

Each tool is a self-contained unit: name, human-readable description, JSON
Schema for inputs, and a handler that produces a string the model can
read back.

The schema converters produce the wire format each LLM provider expects.
Both happen to be very close — Anthropic uses `input_schema`, Ollama
nests it as `function.parameters` inside a `type: function` wrapper —
but neither change is conditional on the schema content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    # JSON Schema dict for the input args. Use {"type": "object", "properties": {...}}
    # plus an optional "required" list. Models tolerate empty schemas for arg-less tools.
    input_schema: dict
    # Handler receives the validated dict of args (may be empty) and returns
    # a string to splice back as the tool_result content. Returning a string
    # NOT a dict — the model handles arbitrary text fine, and dicts add a
    # serialization step on every tool round.
    handler: Callable[[dict], str]

    def to_anthropic(self) -> dict:
        """Anthropic messages API tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_ollama(self) -> dict:
        """Ollama /api/chat tool format. Wrapped in a `type: function`
        envelope; OpenAI-compatible."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
