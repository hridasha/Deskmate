"""Multi-provider fallback and the daily quota picture shown in Settings."""
import pytest

import llm


class Boom:
    def __init__(self, err):
        self.err = err

    async def ainvoke(self, messages, config=None):
        raise self.err


class Ok:
    async def ainvoke(self, messages, config=None):
        return "fine"


@pytest.fixture(autouse=True)
def reset(clean_db):
    llm._cooldown.clear()
    llm.clear_key_cache()
    yield
    llm._cooldown.clear()
    llm.clear_key_cache()


def keys(monkeypatch, **by_provider):
    monkeypatch.setattr(llm, "keys_for", lambda p: by_provider.get(p, []))
    for var, value in (("GEMINI_MODELS", "g1,g2,g3"), ("GEMINI_MODELS_SMART", "g1,g2,g3"), ("GROQ_MODELS", "a1,a2"), ("OPENROUTER_MODELS", "b1,b2"), ("CEREBRAS_MODELS", "c1,c2")):
        monkeypatch.setenv(var, value)  # the developer's own .env must not change what the test expects


def test_every_fallback_provider_with_a_key_becomes_a_candidate(monkeypatch):
    keys(monkeypatch, gemini=["g" * 30], groq=["q" * 30], openrouter=["o" * 30], cerebras=["c" * 30])
    r = llm.get_llm()
    owners = set(r.providers.values())
    assert owners == {"groq", "openrouter", "cerebras"}
    assert len(r.candidates) == 3 + 2 + 2 + 2  # three gemini models, two models for each fallback provider


def test_several_keys_for_one_provider_all_get_used(monkeypatch):
    keys(monkeypatch, groq=["a" * 30, "b" * 30])
    r = llm.get_llm()
    assert len({k for k, _, _ in r.candidates}) == 2


@pytest.mark.asyncio
async def test_when_gemini_is_out_of_quota_a_fallback_answers_and_both_are_counted(monkeypatch):
    keys(monkeypatch, gemini=["g" * 30], groq=["q" * 30])
    router = llm.QuotaAwareLLM(
        [("g" * 30, "gemini-x", Boom(RuntimeError("429 RESOURCE_EXHAUSTED retry in 60s"))), ("q" * 30, "llama-x", Ok())],
        {(llm._kid("q" * 30), "llama-x"): "groq"},
    )
    assert await router.ainvoke("hi") == "fine"
    rows = {(r["provider"], r["model"]): r for r in llm.db.q("SELECT * FROM llm_daily")}
    assert rows[("gemini", "gemini-x")]["quota_hits"] == 1 and rows[("groq", "llama-x")]["calls"] == 1
    assert llm.is_cooling("g" * 30, "gemini-x")  # and Gemini isn't retried on the next message


def test_quota_status_estimates_what_is_left(monkeypatch):
    keys(monkeypatch, gemini=["a" * 30, "b" * 30])
    monkeypatch.setenv("GEMINI_MODELS", "m1")
    monkeypatch.setenv("GEMINI_MODELS_SMART", "m1")
    for _ in range(5):
        llm.record_call("gemini", "m1", "a" * 30, "ok")
    s = llm.quota_status()
    g = next(p for p in s["providers"] if p["provider"] == "gemini")
    row = g["models"][0]
    assert row["calls_today"] == 5 and row["estimated_left"] == 20 * 2 - 5 and s["level"] == "ok"


def test_a_cooling_key_counts_as_empty_and_all_empty_means_exhausted(monkeypatch):
    keys(monkeypatch, gemini=["a" * 30])
    monkeypatch.setenv("GEMINI_MODELS", "m1")
    monkeypatch.setenv("GEMINI_MODELS_SMART", "m1")
    llm.cool_down("a" * 30, "m1", RuntimeError("429 quota retry in 3600s"))
    s = llm.quota_status()
    assert s["level"] == "exhausted" and s["providers"][0]["models"][0]["available_in_s"] > 3000


def test_no_keys_means_none(monkeypatch):
    keys(monkeypatch)
    assert llm.quota_status()["level"] == "none"


def test_quota_endpoint_and_new_providers_are_accepted(clean_db):
    from fastapi.testclient import TestClient

    import main

    c = TestClient(main.app)
    assert c.get("/quota").json()["level"] == "none"
    assert set(c.get("/health").json()["llm"]) == set(llm.PROVIDERS)
    assert c.post("/settings/keys", json={"provider": "cerebras", "key": "x" * 30}).status_code == 400  # rejected by the provider check, not by validation
