"""Provider adapters (raw HTTP, no vendor SDKs). They only accept a GuardedRequest: raw data cannot reach them."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.core.retry import retry_async
from app.privacy.guard import GuardedRequest


class LLMError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status, self.retryable = status, retryable


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. Static (no customer data), so it is sent as-is next to the GuardedRequest."""
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict  # as the model wrote them: privacy placeholders still in place


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    provider: str
    model: str
    latency_ms: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)


_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMProvider(ABC):
    name: str
    default_base: str

    def __init__(self, api_key: str, *, base_url: str | None = None, timeout_s: float = 30.0, retries: int = 1):
        self.api_key, self.base_url, self.timeout_s, self.retries = api_key, (base_url or self.default_base).rstrip("/"), timeout_s, retries

    @abstractmethod
    def _build(self, req: GuardedRequest, model: str, max_tokens: int, temperature: float | None) -> tuple[str, dict, dict]:
        """-> (url, headers, json body)"""

    @abstractmethod
    def _parse(self, data: dict) -> tuple[str, int, int]:
        """-> (text, tokens_in, tokens_out)"""

    def _add_tools(self, body: dict, tools: list[ToolSpec]) -> None:
        raise LLMError(f"{self.name}: tool calling is not supported")

    def _tool_calls(self, data: dict) -> list[ToolCall]:
        return []

    async def generate(self, req: GuardedRequest, *, model: str, max_tokens: int = 400, temperature: float | None = 0.2,
                       stream: bool = False, tools: list[ToolSpec] | None = None) -> LLMResult:
        if not isinstance(req, GuardedRequest):
            raise PermissionError("LLM providers accept only PrivacyGuard output")
        if stream:
            raise NotImplementedError("streaming is not implemented; the pipeline uses complete responses")
        url, headers, body = self._build(req, model, max_tokens, temperature)
        if tools:
            self._add_tools(body, tools)
        t0 = time.perf_counter()

        async def once() -> dict:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                    r = await c.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise LLMError(f"{self.name} network error: {type(exc).__name__}", retryable=True) from exc
            if r.status_code >= 400:
                try:
                    msg = (r.json().get("error") or {}).get("message") or r.text[:200]
                except ValueError:
                    msg = r.text[:200]
                raise LLMError(f"{self.name} {r.status_code}: {msg}", status=r.status_code,
                               retryable=r.status_code in _RETRY_STATUS)
            try:
                return r.json()
            except ValueError as exc:
                raise LLMError(f"{self.name}: invalid JSON response", retryable=True) from exc

        data = await retry_async(once, retries=self.retries, is_retryable=lambda e: isinstance(e, LLMError) and e.retryable,
                                 base_delay=0.4)
        try:
            text, tin, tout = self._parse(data)
            calls = self._tool_calls(data) if tools else []
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape") from exc
        return LLMResult(text=text.strip(), tokens_in=tin, tokens_out=tout, provider=self.name, model=model,
                         latency_ms=int((time.perf_counter() - t0) * 1000), tool_calls=calls)


class AnthropicProvider(LLMProvider):
    name, default_base = "anthropic", "https://api.anthropic.com"

    def _build(self, req, model, max_tokens, temperature):
        body = {"model": model, "max_tokens": max_tokens,
                # static system prompt first + cache_control => prompt caching on the stable prefix
                "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
                "messages": _anthropic_messages(req.messages)}
        if temperature is not None:
            body["temperature"] = temperature
        return (f"{self.base_url}/v1/messages",
                {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, body)

    def _parse(self, d):
        text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
        u = d.get("usage", {})
        tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        return text, tin, u.get("output_tokens", 0)

    def _add_tools(self, body, tools):
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]

    def _tool_calls(self, d):
        return [ToolCall(b["id"], b["name"], dict(b.get("input") or {})) for b in d["content"] if b.get("type") == "tool_use"]


class OpenAIProvider(LLMProvider):
    name, default_base = "openai", "https://api.openai.com"

    def _build(self, req, model, max_tokens, temperature):
        body = {"model": model, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": req.system}, *_openai_messages(req.messages)]}  # static prefix: caching
        if temperature is not None:
            body["temperature"] = temperature
        return f"{self.base_url}/v1/chat/completions", {"authorization": f"Bearer {self.api_key}"}, body

    def _parse(self, d):
        u = d.get("usage", {})
        return d["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

    def _add_tools(self, body, tools):
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                           "parameters": t.parameters}} for t in tools]

    def _tool_calls(self, d):
        return [ToolCall(c["id"], c["function"]["name"], json.loads(c["function"].get("arguments") or "{}"))
                for c in d["choices"][0]["message"].get("tool_calls") or []]


class GoogleProvider(LLMProvider):
    name, default_base = "google", "https://generativelanguage.googleapis.com"

    def _build(self, req, model, max_tokens, temperature):
        contents = _google_contents(req.messages)
        cfg = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            cfg["temperature"] = temperature
        body = {"system_instruction": {"parts": [{"text": req.system}]}, "contents": contents, "generationConfig": cfg}
        return f"{self.base_url}/v1beta/models/{model}:generateContent", {"x-goog-api-key": self.api_key}, body

    def _parse(self, d):
        parts = d["candidates"][0]["content"]["parts"]
        u = d.get("usageMetadata", {})
        return "".join(p.get("text", "") for p in parts), u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)

    def _add_tools(self, body, tools):
        body["tools"] = [{"functionDeclarations": [{"name": t.name, "description": t.description, "parameters": t.parameters}
                                                   for t in tools]}]

    def _tool_calls(self, d):
        parts = d["candidates"][0]["content"].get("parts") or []
        return [ToolCall(f"{p['functionCall']['name']}-{i}", p["functionCall"]["name"], dict(p["functionCall"].get("args") or {}))
                for i, p in enumerate(parts) if "functionCall" in p]


# ---- provider-neutral tool turns -> each vendor's wire format ---------------------------------------------------
# GuardedRequest.messages may hold {"role": "assistant", "content": str, "tool_calls": [{id, name, arguments}]} and
# {"role": "tool", "tool_call_id", "name", "content"} (added by PrivacyGuard.with_tool_results).
def _anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.get("tool_calls"):
            blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append(m)
    return out


def _openai_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append(m)
    return out


def _google_contents(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.get("tool_calls"):
            out.append({"role": "model", "parts": ([{"text": m["content"]}] if m.get("content") else []) + [
                {"functionCall": {"name": c["name"], "args": c["arguments"]}} for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            part = {"functionResponse": {"name": m["name"], "response": {"result": m["content"]}}}
            if out and out[-1]["role"] == "user" and "functionResponse" in out[-1]["parts"][0]:
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
        else:
            out.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]})
    return out


PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {c.name: c for c in (AnthropicProvider, OpenAIProvider, GoogleProvider)}
