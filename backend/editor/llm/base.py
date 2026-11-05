"""
Provider-neutral chat + tool-calling interface.

The agent keeps its conversation in this neutral shape; each provider adapter
translates to and from its own API. Adding the second provider = one new adapter.

Message shapes:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str | None, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema (object)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None          # None when the model sent unparseable JSON
    raw_arguments: str = ""


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> AssistantTurn:
        ...

    def vision_json(self, system: str, prompt: str, images: list[bytes]) -> dict:
        """Look at JPEG images and answer with a JSON object."""
        ...


class LLMError(RuntimeError):
    pass
