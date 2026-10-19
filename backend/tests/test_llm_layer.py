import json
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from app.llm.providers import AnthropicProvider, GoogleProvider, LLMError, LLMResult, OpenAIProvider
from app.llm.service import LLMService, LLMUnavailable, budget_state
from app.models import LLMCall
from app.privacy.guard import PrivacyGuard, PromptInputs, ProviderPolicy
from app.services.settings_service import set_setting
from tests.conftest import seed_llm

PHONE = "01712345678"


def guarded(question=f"call me on {PHONE}", policy=None):
    return PrivacyGuard(policy or ProviderPolicy()).prepare(PromptInputs(question=question, chunks=("We open 9-5.",)))


# ---------------------------------------------------------------- adapters
@respx.mock
async def test_anthropic_request_shape_prompt_caching_and_usage():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(200, json={
        "content": [{"type": "text", "text": " Hello "}],
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 7}}))
    res = await AnthropicProvider("k").generate(guarded(), model="claude-haiku-4-5-20251001", max_tokens=123, temperature=0.1)
    body = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["x-api-key"] == "k" and "anthropic-version" in route.calls[0].request.headers
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["max_tokens"] == 123
    assert body["messages"][-1]["role"] == "user" and PHONE not in json.dumps(body)
    assert (res.text, res.tokens_in, res.tokens_out) == ("Hello", 150, 7)
    assert body["temperature"] == 0.1 and "thinking" not in body                 # an older model still takes temperature


@pytest.mark.parametrize("model, thinking, max_tokens", [
    ("claude-sonnet-5-5", {"type": "between_tools"}, 123),   # Sonnet 5.5: thinking off without "disabled"
    ("claude-haiku-5-5", {"type": "disabled"}, 123),
    ("claude-opus-4-8", {"type": "disabled"}, 123),
    ("claude-opus-5-5", None, 4000),                          # thinking always on: room for it and the reply
])
@respx.mock
async def test_newer_claude_models_get_effort_instead_of_temperature(model, thinking, max_tokens):
    route = respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(200, json={
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "ok"}], "usage": {}}))
    res = await AnthropicProvider("k").generate(guarded(), model=model, max_tokens=123, temperature=0.2)
    body = json.loads(route.calls[0].request.content)
    assert "temperature" not in body and body["output_config"] == {"effort": "low"}
    assert body.get("thinking") == thinking and body["max_tokens"] == max_tokens and res.text == "ok"


@respx.mock
async def test_openai_and_google_shapes():
    o = respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}))
    r = await OpenAIProvider("ok").generate(guarded(), model="m", max_tokens=50)
    ob = json.loads(o.calls[0].request.content)
    assert ob["messages"][0]["role"] == "system" and ob["max_completion_tokens"] == 50
    assert o.calls[0].request.headers["authorization"] == "Bearer ok" and (r.tokens_in, r.tokens_out) == (11, 3)
    g = respx.post("https://generativelanguage.googleapis.com/v1beta/models/gem:generateContent").mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}],
                                               "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2}}))
    r = await GoogleProvider("gk").generate(guarded(), model="gem", max_tokens=50)
    gb = json.loads(g.calls[0].request.content)
    assert g.calls[0].request.headers["x-goog-api-key"] == "gk" and gb["system_instruction"]["parts"][0]["text"]
    assert gb["contents"][-1]["role"] == "user" and r.text == "ab" and (r.tokens_in, r.tokens_out) == (9, 2)


async def test_providers_reject_non_guarded_input_and_streaming():
    p = AnthropicProvider("k")
    with pytest.raises(PermissionError):
        await p.generate({"system": "s", "messages": [{"role": "user", "content": "raw phone 0171..."}]}, model="m")  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await p.generate(guarded(), model="m", stream=True)


@respx.mock
async def test_retryable_errors_are_retried_then_succeed(monkeypatch):
    import app.core.retry as retry
    async def no_sleep(_): return None
    monkeypatch.setattr(retry.asyncio, "sleep", no_sleep)
    seq = [httpx.Response(529, json={"error": {"message": "overloaded"}}),
           httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}})]
    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=seq)
    assert (await AnthropicProvider("k", retries=2).generate(guarded(), model="m")).text == "ok"


@respx.mock
async def test_non_retryable_error_raises_immediately():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError) as ei:
        await AnthropicProvider("k", retries=3).generate(guarded(), model="m")
    assert ei.value.status == 401 and not ei.value.retryable and route.call_count == 1


@respx.mock
async def test_timeout_maps_to_retryable_llm_error():
    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as ei:
        await AnthropicProvider("k", retries=0).generate(guarded(), model="m")
    assert ei.value.retryable


# ---------------------------------------------------------------- routing service
class Fake:
    """Provider factory whose behaviour is scripted per provider name."""
    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, name, key, base, timeout, retries):
        outer = self

        class P:
            async def generate(self, req, *, model, max_tokens, temperature):
                outer.calls.append((name, model, max_tokens, req))
                r = outer.script[name]
                if isinstance(r, Exception):
                    raise r
                return LLMResult(text=r, tokens_in=1000, tokens_out=200, provider=name, model=model, latency_ms=5)
        return P()


ROUTES = {"answer": ("anthropic", "small", "openai", "fb-model")}
PRICES = {("anthropic", "small"): (1, 5), ("openai", "fb-model"): (2, 10), ("google", "cheap"): (0.1, 0.4)}


async def test_success_logs_cost_tokens_and_exact_post_filter_payload(maker, session):
    await seed_llm(session, routes=ROUTES, prices=PRICES)
    f = Fake({"anthropic": "answer"})
    svc = LLMService(maker, f)
    out = await svc.run(session, "answer", lambda pol: guarded(), channel="messenger", conversation_id=7)
    assert out.result.text == "answer" and out.cost_usd == Decimal("0.002000")   # 1000*1 + 200*5 per 1M
    assert f.calls[0][2] == 300                                                 # max_tokens from the route
    async with maker() as s:
        row = (await s.execute(select(LLMCall))).scalar_one()
    assert (row.provider, row.model, row.tokens_in, row.tokens_out, row.status, row.channel) == ("anthropic", "small", 1000, 200, "ok", "messenger")
    assert PHONE not in json.dumps(row.request_json) and "[PHONE_1]" in json.dumps(row.request_json)   # audit = what was sent
    assert row.redactions and row.latency_ms == 5 and not row.fallback_used


async def test_error_triggers_fallback_and_both_attempts_are_logged(maker, session):
    await seed_llm(session, providers=("anthropic", "openai"), routes=ROUTES, prices=PRICES)
    f = Fake({"anthropic": __import__("app.llm.providers", fromlist=["LLMError"]).LLMError("boom", status=500, retryable=True),
              "openai": "from fallback"})
    out = await LLMService(maker, f).run(session, "answer", lambda pol: guarded())
    assert out.result.text == "from fallback" and out.fallback_used
    async with maker() as s:
        rows = (await s.execute(select(LLMCall).order_by(LLMCall.id))).scalars().all()
    assert [(r.provider, r.status, r.fallback_used) for r in rows] == [("anthropic", "error", False), ("openai", "ok", True)]


async def test_each_attempt_is_rebuilt_with_that_providers_pii_policy(maker, session):
    from app.llm.providers import LLMError
    await seed_llm(session, providers=("anthropic", "openai"), allow_pii={"openai": True}, routes=ROUTES, prices=PRICES)
    f = Fake({"anthropic": LLMError("down"), "openai": "x"})
    seen = []

    def build(pol):
        seen.append(pol.allow_pii)
        return guarded(policy=pol)
    await LLMService(maker, f).run(session, "answer", build)
    assert seen == [False, True]
    assert PHONE not in json.dumps(f.calls[0][3].audit_payload)   # strict provider: redacted
    assert PHONE in json.dumps(f.calls[1][3].audit_payload)       # trusted provider: contacts may pass (audit shows it)


async def test_all_failed_or_unconfigured_raises_unavailable(maker, session):
    from app.llm.providers import LLMError
    await seed_llm(session, routes=ROUTES)  # only anthropic enabled; fallback openai is not configured
    with pytest.raises(LLMUnavailable):
        await LLMService(maker, Fake({"anthropic": LLMError("down")})).run(session, "answer", lambda p: guarded())
    with pytest.raises(LLMUnavailable):
        await LLMService(maker, Fake({})).run(session, "unknown-task", lambda p: guarded())


async def test_budget_alert_exceeded_switches_to_cheap_model_and_hard_stop(maker, session):
    await seed_llm(session, providers=("anthropic", "google"), routes=ROUTES, prices=PRICES)
    await set_setting(session, "monthly_budget_usd", 0.0024)
    await set_setting(session, "budget_fallback_route", {"provider": "google", "model": "cheap"})
    await session.commit()
    f = Fake({"anthropic": "big", "google": "cheap-answer"})
    svc = LLMService(maker, f)
    first = await svc.run(session, "answer", lambda p: guarded())          # costs 0.002 of 0.0024 = 83%
    assert first.result.text == "big" and (await budget_state(session)).level == "alert"
    await svc.run(session, "answer", lambda p: guarded())                  # now 0.004 > budget
    assert (await budget_state(session)).level == "exceeded"
    third = await svc.run(session, "answer", lambda p: guarded())
    assert third.result.text == "cheap-answer" and third.budget_fallback
    await set_setting(session, "budget_hard_stop", True)
    await session.commit()
    with pytest.raises(LLMUnavailable):
        await svc.run(session, "answer", lambda p: guarded())


async def test_test_calls_are_flagged_and_logged_in_their_own_transaction(maker, session):
    await seed_llm(session, routes=ROUTES, prices=PRICES)
    await LLMService(maker, Fake({"anthropic": "a"})).run(session, "answer", lambda p: guarded(), is_test=True)
    await session.rollback()  # the caller rolls back (Test Chat "do not save"): the cost row must survive
    async with maker() as s:
        assert (await s.execute(select(LLMCall.is_test))).scalar_one() is True
