"""LLM layer: Gemini (rotating keys, model tiers) with fallback providers (Groq, OpenRouter, Cerebras), structured output and retries.

Keys come from the environment (.env) and from the encrypted store managed in the app's Settings screen.
"""
import asyncio
import hashlib
import itertools
import os
import random
import re
import time
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

import db

load_dotenv()

_DEFAULT_FAST = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash"
_DEFAULT_SMART = "gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"


def _models(tier: str) -> list[str]:
    var = "GEMINI_MODELS_SMART" if tier == "smart" else "GEMINI_MODELS"
    default = _DEFAULT_SMART if tier == "smart" else _DEFAULT_FAST
    return [m.strip() for m in os.getenv(var, default).split(",") if m.strip()]


# Providers after Gemini are fallbacks. Each key may come from a different account, which is how you get past one free tier's daily cap.
PROVIDERS: dict[str, dict] = {
    "gemini": {"label": "Google Gemini", "env": "GEMINI_API_KEYS", "daily": 20},
    "groq": {"label": "Groq", "env": "GROQ_API_KEY", "models": ("GROQ_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant"), "daily": 1000},
    "openrouter": {"label": "OpenRouter", "env": "OPENROUTER_API_KEY", "models": ("OPENROUTER_MODELS", "meta-llama/llama-3.3-70b-instruct:free,google/gemma-3-27b-it:free"), "base_url": "https://openrouter.ai/api/v1", "daily": 50},
    "cerebras": {"label": "Cerebras", "env": "CEREBRAS_API_KEY", "models": ("CEREBRAS_MODELS", "llama-3.3-70b,llama3.1-8b"), "base_url": "https://api.cerebras.ai/v1", "daily": 1000},
}
FALLBACKS = [p for p in PROVIDERS if p != "gemini"]


def provider_models(provider: str) -> list[str]:
    var, default = PROVIDERS[provider]["models"]
    raw = os.getenv(var) or os.getenv(var.removesuffix("S"), "") or default  # GROQ_MODEL (old, single) still works
    return [m.strip() for m in raw.split(",") if m.strip()]


def daily_limit(provider: str) -> int:
    """Requests per key and model per day we expect from the free tier (override with DAILY_LIMITS_JSON='{"gemini": 250}')."""
    import json

    try:
        return int(json.loads(os.getenv("DAILY_LIMITS_JSON", "{}")).get(provider, PROVIDERS[provider]["daily"]))
    except (ValueError, TypeError):
        return PROVIDERS[provider]["daily"]


_rotation = itertools.count()
_key_cache: dict[str, tuple[float, list[str]]] = {}


def _env_keys(var: str) -> list[str]:
    return [k.strip() for k in os.getenv(var, "").split(",") if k.strip()]


def keys_for(provider: str) -> list[str]:
    """All usable keys for a provider: .env first, then keys saved through the app. Cached for a few seconds."""
    hit = _key_cache.get(provider)
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    env_var = PROVIDERS.get(provider, {}).get("env", "")
    keys = _env_keys(env_var) if env_var else []
    for _, secret in db.api_key_secrets(provider):
        if secret not in keys:
            keys.append(secret)
    _key_cache[provider] = (time.time(), keys)
    return keys


def clear_key_cache() -> None:
    _key_cache.clear()


def env_key_rows() -> list[dict]:
    """Keys supplied through .env, shown read-only (masked) in Settings."""
    import secrets_store

    rows = []
    for provider, var in ((p, c["env"]) for p, c in PROVIDERS.items()):
        for i, k in enumerate(_env_keys(var)):
            rows.append({"id": f"env-{provider}-{i}", "provider": provider, "label": ".env", "masked": secrets_store.mask(k), "source": "env", "status": "unknown", "disabled": 0})
    return rows


# ---------------- quota awareness ----------------
# Free tiers allow only a handful of requests per day per key and model. Once a key/model pair is out of quota (or
# overloaded) we remember for how long, skip it, and move on, instead of retrying it on every request.

_cooldown: dict[tuple[str, str], tuple[float, str]] = {}  # (key id, model) -> (until, reason)
MAX_COOLDOWN_S = 6 * 3600


def record_call(provider: str, model: str, key: str, outcome: str) -> None:
    """Count a model call for today ('ok', 'quota' for a rate-limit hit, or 'error'), so Settings can show what's left."""
    col = {"ok": "calls", "quota": "quota_hits"}.get(outcome, "errors")
    try:
        db.run(f"INSERT INTO llm_daily (day, provider, model, kid, {col}) VALUES (?,?,?,?,1) ON CONFLICT(day, provider, model, kid) DO UPDATE SET {col} = {col} + 1", (time.strftime("%Y-%m-%d"), provider, model, _kid(key)))
    except Exception:  # noqa: BLE001  never let bookkeeping break a chat
        pass


class QuotaExhausted(RuntimeError):
    """Every available model and key is cooling down."""

    def __init__(self, retry_in_s: int):
        self.retry_in_s = retry_in_s
        super().__init__(f"All AI models are out of quota for now. The soonest one is available in about {_human(retry_in_s)}.")


def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{round(seconds / 3600, 1)} hours"


def _kid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def retry_after_seconds(message: str) -> float | None:
    """Read the provider's own 'retry in 7h27m31s' / 'retryDelay: 26851s' hint."""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", message)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", message, re.IGNORECASE)
    if m and any(m.groups()):
        h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + sec
    return None


def _kind(err: Exception) -> str | None:
    """'quota' for exhausted limits, 'busy' for overload, None for errors that cooling down won't fix."""
    text = f"{type(err).__name__} {err}".lower()
    if "resource_exhausted" in text or "429" in text or "quota" in text or "rate limit" in text:
        return "quota"
    if "503" in text or "unavailable" in text or "overloaded" in text or "high demand" in text:
        return "busy"
    return None


def cool_down(key: str, model: str, err: Exception) -> None:
    kind = _kind(err)
    if not kind:
        return
    hinted = retry_after_seconds(str(err))
    seconds = min(hinted if hinted else (300 if kind == "quota" else 30), MAX_COOLDOWN_S)
    _cooldown[(_kid(key), model)] = (time.time() + seconds, kind)


def is_cooling(key: str, model: str) -> bool:
    hit = _cooldown.get((_kid(key), model))
    return bool(hit and hit[0] > time.time())


def cooldown_status() -> list[dict]:
    """For the Health screen: per model, how many keys are available right now."""
    keys = keys_for("gemini")
    now = time.time()
    models = sorted({m for tier in ("fast", "smart") for m in _models(tier)} | {EMBED_MODEL_NAME})
    out = []
    for model in models:
        waits = [max(0, _cooldown[(_kid(k), model)][0] - now) for k in keys if (_kid(k), model) in _cooldown and _cooldown[(_kid(k), model)][0] > now]
        out.append({"model": model, "keys": len(keys), "cooling": len(waits), "available_in_s": int(min(waits)) if waits and len(waits) == len(keys) else 0})
    return out


def quota_status() -> dict:
    """What is left today, per provider and model, from our own call counts and the cooldowns we have learned.

    The remaining number is an estimate: it assumes each key belongs to its own account or project. Several keys from
    one Google project share ONE quota, and the provider's real limit can change, so treat it as a guide.
    """
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    counts = {(r["provider"], r["model"], r["kid"]): r for r in db.q("SELECT * FROM llm_daily WHERE day=?", (today,))}
    providers = []
    total_left = total_cap = 0
    for provider, cfg in PROVIDERS.items():
        keys = keys_for(provider)
        if not keys:
            providers.append({"provider": provider, "label": cfg["label"], "keys": 0, "models": []})
            continue
        models = list(dict.fromkeys(_models("smart") + _models("fast"))) if provider == "gemini" else provider_models(provider)
        cap = daily_limit(provider)
        rows = []
        for model in models:
            calls = quota_hits = errors = left = cooling = 0
            wait = None
            for k in keys:
                c = counts.get((provider, model, _kid(k)), {})
                used = c.get("calls", 0) or 0
                calls += used
                quota_hits += c.get("quota_hits", 0) or 0
                errors += c.get("errors", 0) or 0
                hit = _cooldown.get((_kid(k), model))
                if hit and hit[0] > now:
                    cooling += 1
                    wait = min(wait, hit[0] - now) if wait is not None else hit[0] - now
                    continue  # a key the provider has refused counts as empty until the cooldown ends
                left += max(0, cap - used)
            total_left += left
            total_cap += cap * len(keys)
            rows.append({"model": model, "calls_today": calls, "quota_hits": quota_hits, "errors": errors, "keys": len(keys), "cooling": cooling, "estimated_left": left, "available_in_s": int(wait) if wait is not None and cooling == len(keys) else 0})
        providers.append({"provider": provider, "label": cfg["label"], "keys": len(keys), "daily_per_key": cap, "models": rows})
    ratio = (total_left / total_cap) if total_cap else 0
    level = "none" if not total_cap else "exhausted" if total_left == 0 else "low" if ratio < 0.2 else "ok"
    return {"level": level, "estimated_left": total_left, "providers": providers}


EMBED_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")


class QuotaAwareLLM:
    """Tries candidates in order, skipping any that are cooling down, and learns from failures."""

    def __init__(self, candidates: list[tuple[str, str, Any]], providers: dict[tuple[str, str], str] | None = None):  # (key, model name, runnable)
        self.candidates = candidates
        self.providers = providers or {}

    async def ainvoke(self, messages: Any, config: Any = None):
        last: Exception | None = None
        soonest: float | None = None
        for key, model, runnable in self.candidates:
            hit = _cooldown.get((_kid(key), model))
            if hit and hit[0] > time.time():
                soonest = min(soonest, hit[0] - time.time()) if soonest is not None else hit[0] - time.time()
                continue
            provider = self.providers.get((_kid(key), model), "gemini")
            try:
                result = await runnable.ainvoke(messages, config)
                record_call(provider, model, key, "ok")
                return result
            except Exception as e:  # noqa: BLE001 - provider errors vary widely
                last = e
                cool_down(key, model, e)
                record_call(provider, model, key, "quota" if _kind(e) == "quota" else "error")
        if last is None:
            raise QuotaExhausted(int(soonest or 60))
        if _kind(last) == "quota" and all(is_cooling(k, m) for k, m, _ in self.candidates):
            raise QuotaExhausted(int(min(_cooldown[(_kid(k), m)][0] - time.time() for k, m, _ in self.candidates)))
        raise last


async def with_key(provider: str, model: str, call: Callable[[str], Awaitable[Any]]):
    """Run a call with a key that has quota, rotating past exhausted ones (used for embeddings)."""
    keys = keys_for(provider)
    if not keys:
        raise RuntimeError(f"No {provider} key configured.")
    offset = next(_rotation)
    last: Exception | None = None
    for i in range(len(keys)):
        key = keys[(offset + i) % len(keys)]
        if is_cooling(key, model):
            continue
        try:
            return await call(key)
        except Exception as e:  # noqa: BLE001
            last = e
            cool_down(key, model, e)
    raise last or QuotaExhausted(60)


def _wrap(model: Any, tools: list | None, schema: Any | None):
    if schema is not None:
        return model.with_structured_output(schema)
    return model.bind_tools(tools) if tools else model


def _chat_client(provider: str, model: str, key: str):
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=key, temperature=0.8, timeout=20)
    from langchain_openai import ChatOpenAI  # OpenRouter and Cerebras speak the OpenAI protocol

    return ChatOpenAI(model=model, api_key=key, base_url=PROVIDERS[provider]["base_url"], temperature=0.8, timeout=20, max_retries=0)


def get_llm(tools: list | None = None, tier: str = "fast", schema: Any | None = None):
    """A runnable that tries the preferred model first and falls back through other models, keys and Groq."""
    candidates: list[tuple[str, str, Any]] = []
    gkeys = keys_for("gemini")
    if gkeys:
        offset = next(_rotation)
        for name in _models(tier):
            # every key is a candidate for every model (the quota-aware router skips the exhausted ones)
            for i in range(len(gkeys)):
                key = gkeys[(offset + i) % len(gkeys)]
                if is_cooling(key, name):
                    continue  # don't even build clients for pairs we know are out of quota
                m = ChatGoogleGenerativeAI(model=name, google_api_key=key, temperature=0.8, max_retries=0, timeout=20)
                candidates.append((key, name, _wrap(m, tools, schema)))
    owners: dict[tuple[str, str], str] = {}
    other_keys = False
    for provider in FALLBACKS:
        pkeys = keys_for(provider)
        other_keys = other_keys or bool(pkeys)
        for name in provider_models(provider) if pkeys else []:
            for key in pkeys:
                if is_cooling(key, name):
                    continue
                candidates.append((key, name, _wrap(_chat_client(provider, name, key), tools, schema)))
                owners[(_kid(key), name)] = provider
    if not candidates:
        if gkeys or other_keys:  # keys exist, but every pair is cooling down
            waits = [v[0] - time.time() for v in _cooldown.values() if v[0] > time.time()]
            raise QuotaExhausted(int(min(waits)) if waits else 60)
        raise RuntimeError("No LLM configured. Add a Gemini or Groq API key in Settings (or in backend/.env).")
    return QuotaAwareLLM(candidates, owners)


TRANSIENT = ("429", "503", "500", "unavailable", "resource_exhausted", "timeout", "timed out", "overloaded", "rate limit", "deadline", "connection")


def is_transient(err: Exception) -> bool:
    if isinstance(err, QuotaExhausted):
        return False  # waiting a few seconds won't help; the message says how long it will take
    text = f"{type(err).__name__} {err}".lower()
    return any(t in text for t in TRANSIENT)


async def ainvoke_retry(llm: Any, messages: Any, config: Any = None, attempts: int = 3):
    """Call the model, retrying transient failures (rate limits, overload) with exponential backoff and jitter."""
    last: Exception | None = None
    for n in range(attempts):
        try:
            return await llm.ainvoke(messages, config)
        except Exception as e:  # noqa: BLE001 - provider errors vary widely
            last = e
            if not is_transient(e) or n == attempts - 1:
                raise
            await asyncio.sleep((1.6**n) + random.random() * 0.5)
    raise last  # pragma: no cover


async def validate_key(provider: str, key: str) -> tuple[bool, str]:
    """Check a key with a free metadata request (lists models); no tokens are spent."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if provider == "gemini":
                r = await client.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 1})
            elif provider == "groq":
                r = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"})
            elif provider == "openrouter":
                r = await client.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"})
            elif provider == "cerebras":
                r = await client.get("https://api.cerebras.ai/v1/models", headers={"Authorization": f"Bearer {key}"})
            else:
                return True, "Saved (this provider has no live check)"
        if r.status_code == 200:
            return True, "Key works"
        if r.status_code in (400, 401, 403):
            return False, "The provider rejected this key"
        return False, f"Provider returned HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"Couldn't reach the provider: {type(e).__name__}"
