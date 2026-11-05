"""LLM providers for the editing agent. Selected with EDITOR_LLM_PROVIDER (default: openai)."""

import os

from .base import AssistantTurn, LLMError, LLMProvider, ToolCall, ToolSpec

__all__ = ["AssistantTurn", "LLMError", "LLMProvider", "ToolCall", "ToolSpec", "get_provider"]


def get_provider() -> LLMProvider:
    name = os.getenv("EDITOR_LLM_PROVIDER", "openai").lower()
    if name == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set")
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=key,
            model=os.getenv("EDITOR_AGENT_MODEL", "gpt-5-mini"),
            reasoning_effort=os.getenv("EDITOR_AGENT_REASONING", "low") or None,
        )
    raise LLMError(f"Unknown EDITOR_LLM_PROVIDER: {name!r} (supported: openai)")
