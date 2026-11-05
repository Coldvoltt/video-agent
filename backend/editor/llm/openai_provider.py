"""OpenAI adapter (Chat Completions with function calling and image input)."""

import base64
import json

from .base import AssistantTurn, LLMError, ToolCall, ToolSpec


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, reasoning_effort: str | None = "low", timeout: float = 120):
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)

    @staticmethod
    def _to_openai(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content")}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments or {})}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> AssistantTurn:
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "developer", "content": system}, *self._to_openai(messages)],
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc

        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args, raw_arguments=raw))
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return AssistantTurn(text=msg.content, tool_calls=calls, usage=usage)

    def vision_json(self, system: str, prompt: str, images: list[bytes]) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            url = "data:image/jpeg;base64," + base64.b64encode(img).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "developer", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            resp = self.client.chat.completions.create(**kwargs)
            return json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError as exc:
            raise LLMError(f"Vision answer was not JSON: {exc}") from exc
        except Exception as exc:
            raise LLMError(f"OpenAI vision request failed: {exc}") from exc
