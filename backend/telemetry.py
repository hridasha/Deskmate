"""Tracing and metrics: every model call and tool call is logged with tokens, latency and status."""
import json
import os
import time
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

import db
import presence

# Optional price table in USD per 1M tokens, e.g. {"gemini-3.8-flash": [0.3, 2.5]}. Free-tier usage costs nothing.
try:
    PRICES: dict[str, list[float]] = json.loads(os.getenv("PRICES_JSON", "{}"))
except json.JSONDecodeError:
    PRICES = {}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICES.get(model)
    return (prompt_tokens * p[0] + completion_tokens * p[1]) / 1_000_000 if p else 0.0


class Telemetry(AsyncCallbackHandler):
    """Attach to a graph run via config={'callbacks': [Telemetry(user, agent)]}."""

    def __init__(self, user: str, agent: str):
        self.user, self.agent = user, agent
        self._llm: dict[Any, tuple[float, str]] = {}
        self._tool: dict[Any, tuple[float, str]] = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._llm[run_id] = (time.time(), (metadata or {}).get("ls_model_name", ""))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        start, model = self._llm.pop(run_id, (time.time(), ""))
        pt = ct = 0
        try:
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
            pt, ct = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            model = model or (getattr(msg, "response_metadata", {}) or {}).get("model_name", "")
        except (IndexError, AttributeError):
            pass
        db.log_trace(self.user, self.agent, "llm", model=model, prompt_tokens=pt, completion_tokens=ct, latency_ms=(time.time() - start) * 1000, cost=estimate_cost(model, pt, ct))

    async def on_llm_error(self, error, *, run_id, **kwargs):
        start, model = self._llm.pop(run_id, (time.time(), ""))
        db.log_trace(self.user, self.agent, "llm", model=model, latency_ms=(time.time() - start) * 1000, status="error", detail=f"{type(error).__name__}: {error}")

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name", "")
        self._tool[run_id] = (time.time(), name)
        presence.step(self.user, self.agent, name)

    async def on_tool_end(self, output, *, run_id, **kwargs):
        start, name = self._tool.pop(run_id, (time.time(), ""))
        db.log_trace(self.user, self.agent, "tool", name=name, latency_ms=(time.time() - start) * 1000)

    async def on_tool_error(self, error, *, run_id, **kwargs):
        start, name = self._tool.pop(run_id, (time.time(), ""))
        db.log_trace(self.user, self.agent, "tool", name=name, latency_ms=(time.time() - start) * 1000, status="error", detail=str(error))


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(p * len(s)))], 1)


def summary(hours: float = 24) -> dict:
    """Aggregated metrics for the Settings > Health screen."""
    since = time.time() - hours * 3600
    rows = db.q("SELECT * FROM traces WHERE ts >= ?", (since,))
    llm = [r for r in rows if r["kind"] == "llm"]
    tools = [r for r in rows if r["kind"] == "tool"]
    plans = [r for r in rows if r["kind"] == "plan"]

    def group(rs: list[dict], key: str) -> list[dict]:
        out: dict[str, list[dict]] = {}
        for r in rs:
            out.setdefault(r[key] or "unknown", []).append(r)
        return [
            {
                key: k,
                "calls": len(v),
                "errors": sum(1 for x in v if x["status"] != "ok"),
                "tokens_in": sum(x["prompt_tokens"] for x in v),
                "tokens_out": sum(x["completion_tokens"] for x in v),
                "p50_ms": _pct([x["latency_ms"] for x in v], 0.5),
                "p95_ms": _pct([x["latency_ms"] for x in v], 0.95),
                "cost": round(sum(x["cost"] for x in v), 6),
            }
            for k, v in sorted(out.items(), key=lambda kv: -len(kv[1]))
        ]

    return {
        "window_hours": hours,
        "llm_calls": len(llm),
        "llm_errors": sum(1 for r in llm if r["status"] != "ok"),
        "tokens_in": sum(r["prompt_tokens"] for r in llm),
        "tokens_out": sum(r["completion_tokens"] for r in llm),
        "cost": round(sum(r["cost"] for r in llm), 6),
        "p50_ms": _pct([r["latency_ms"] for r in llm], 0.5),
        "p95_ms": _pct([r["latency_ms"] for r in llm], 0.95),
        "tool_calls": len(tools),
        "plans_total": len(plans),
        "plans_without_llm": sum(1 for r in plans if r["detail"] in ("router", "explicit")),
        "by_agent": group(llm, "agent"),
        "by_model": group(llm, "model"),
        "by_tool": group(tools, "name"),
    }
