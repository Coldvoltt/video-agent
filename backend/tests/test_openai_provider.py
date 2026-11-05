"""OpenAI adapter against a stub client: request shape and response parsing."""

import json
from types import SimpleNamespace as NS

import pytest

from editor.llm import LLMError, ToolCall, ToolSpec, get_provider
from editor.llm.openai_provider import OpenAIProvider


class StubCompletions:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def provider_with(response) -> tuple[OpenAIProvider, StubCompletions]:
    p = OpenAIProvider(api_key="sk-test", model="gpt-5-mini", reasoning_effort="low")
    stub = StubCompletions(response)
    p.client = NS(chat=NS(completions=stub))
    return p, stub


def response(content=None, tool_calls=()):
    calls = [NS(id=i, function=NS(name=n, arguments=a)) for i, n, a in tool_calls]
    usage = NS(model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 5})
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls or None))], usage=usage)


def test_request_shape_and_tool_call_parsing():
    p, stub = provider_with(response(tool_calls=[("c1", "list_assets", "{}"), ("c2", "set_timeline", "{bad")]))
    history = [
        {"role": "user", "content": "cut it"},
        {"role": "assistant", "content": None,
         "tool_calls": [ToolCall(id="c0", name="get_timeline", arguments={}, raw_arguments="{}")]},
        {"role": "tool", "tool_call_id": "c0", "name": "get_timeline", "content": "{\"timeline\": null}"},
    ]
    tools = [ToolSpec("list_assets", "List media", {"type": "object", "properties": {}})]
    turn = p.complete("SYSTEM", history, tools)

    sent = stub.kwargs
    assert sent["model"] == "gpt-5-mini" and sent["reasoning_effort"] == "low"
    assert sent["messages"][0] == {"role": "developer", "content": "SYSTEM"}
    assert sent["messages"][2]["tool_calls"][0] == {
        "id": "c0", "type": "function", "function": {"name": "get_timeline", "arguments": "{}"}}
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "c0", "content": "{\"timeline\": null}"}
    assert sent["tools"][0]["function"]["name"] == "list_assets"

    assert [c.name for c in turn.tool_calls] == ["list_assets", "set_timeline"]
    assert turn.tool_calls[0].arguments == {} and turn.tool_calls[1].arguments is None  # bad JSON surfaced
    assert turn.usage["prompt_tokens"] == 10


def test_text_reply_and_errors(monkeypatch):
    p, _ = provider_with(response(content="All done."))
    turn = p.complete("S", [{"role": "user", "content": "hi"}], [])
    assert turn.text == "All done." and turn.tool_calls == []

    p, _ = provider_with(RuntimeError("rate limited"))
    with pytest.raises(LLMError, match="rate limited"):
        p.complete("S", [], [])

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        get_provider()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("EDITOR_AGENT_MODEL", "gpt-5")
    assert get_provider().model == "gpt-5"
    monkeypatch.setenv("EDITOR_LLM_PROVIDER", "other")
    with pytest.raises(LLMError, match="Unknown"):
        get_provider()
