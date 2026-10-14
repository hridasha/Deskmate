"""Evaluation harness.

Routing cases run offline and deterministically. Live cases run real conversations through the real pipeline
(model, tools, guard) and check the observable behavior: which tools ran, who got the task, tone and format.

Run from the backend folder:  python -m evals.run [--routing-only] [--limit N] [--judge]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import agent as agents
import db
import router
from llm import ainvoke_retry, get_llm

CASES = Path(__file__).parent / "cases.json"
OUT_DIR = db.DATA_DIR / "eval_runs"


def word_count(text: str) -> int:
    return len(text.split())


async def collect(character: dict, message: str, user: str) -> dict:
    """Run one conversation and gather what happened."""
    out = {"tools": [], "plan": None, "reply": "", "emotion": "", "error": ""}
    try:
        async for ev in agents.agent_events(character, message, user):
            if ev["state"] == "tool_call":
                out["tools"].append(ev["tool"])
            elif ev["state"] == "plan":
                out["plan"] = ev
            elif ev["state"] == "speaking":
                out["reply"], out["emotion"] = ev["text"], ev["emotion"]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def check(case: dict, got: dict, user: str) -> list[dict]:
    exp, reply = case["expect"], got["reply"].lower()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("no error", not got["error"], got["error"])
    for t in exp.get("tool_called", []):
        add(f"called {t}", t in got["tools"], f"tools: {got['tools']}")
    for t in exp.get("tool_not_called", []):
        add(f"did not call {t}", t not in got["tools"], f"tools: {got['tools']}")
    if "note_contains" in exp:
        notes = " ".join(n["text"].lower() for n in db.list_notes(user, 10))
        add("note saved", exp["note_contains"].lower() in notes, notes[:100])
    if exp.get("no_unperformed_claim"):
        claimed = agents.claims_saved(got["reply"])
        add("claims match actions", (not claimed) or bool({"take_note", "remember"} & set(got["tools"])), f"claimed={claimed}, tools={got['tools']}")
    if "max_words" in exp:
        add(f"at most {exp['max_words']} words", word_count(got["reply"]) <= exp["max_words"], f"{word_count(got['reply'])} words")
    for s in exp.get("not_contains", []):
        add(f"does not contain '{s}'", s.lower() not in reply)
    if "contains_all" in exp:
        missing = [t for t in exp["contains_all"] if t not in reply]
        add("contains all expected terms", not missing, f"missing: {missing}")
    if "contains_any" in exp:
        add("mentions expected name", any(s in reply for s in exp["contains_any"]))
    if "not_similar_to" in exp:
        import feedback

        sim = feedback.similarity(got["reply"], exp["not_similar_to"])
        add("not the same answer as the one marked bad", sim < 0.8, f"overlap {sim:.0%}")
    if "emotion_in" in exp:
        add("emotion valid", got["emotion"] in exp["emotion_in"], got["emotion"])
    if exp.get("no_plan"):
        add("no delegation", got["plan"] is None)
    if "plan_agents" in exp:
        planned = [t["agent"] for t in (got["plan"] or {}).get("tasks", [])]
        add("planned the right people", sorted(planned) == sorted(exp["plan_agents"]), f"planned: {planned}")
    if "plan_via" in exp:
        add(f"planned via {exp['plan_via']}", (got["plan"] or {}).get("via") == exp["plan_via"], str((got["plan"] or {}).get("via")))
    return checks


def run_routing(workers: dict[str, dict], cases: list[dict]) -> dict:
    """The router either picks someone confidently (must be right) or defers to the LLM planner (always acceptable)."""
    rt = router.Router(workers)
    rows, confident, correct, answerable = [], 0, 0, 0
    for c in cases:
        scores = rt.scores(c["text"])
        top, conf = scores[0][0], rt.confident(scores)
        if c["expect"] is None:  # ambiguous: the router should defer
            ok, detail = not conf, "should defer to the planner"
        else:
            answerable += 1
            if conf:
                confident += 1
                correct += top == c["expect"]
                ok, detail = top == c["expect"], f"confident, expected {c['expect']}"
            else:
                ok, detail = True, "deferred to the planner (acceptable)"
        rows.append({"id": c["id"], "category": "routing", "passed": ok, "checks": [{"name": f"top={top} ({scores[0][1]:.2f}) confident={conf}", "ok": ok, "detail": detail}]})
    return {
        "rows": rows,
        "precision": round(correct / max(1, confident), 3),  # when confident, how often is it right
        "coverage": round(confident / max(1, answerable), 3),  # how often it is confident (so no LLM planning call is needed)
    }


async def run_status_intent(workers: dict[str, dict], cases: list[dict], head_id: str, user: str) -> dict:
    """Does the Head treat questions about the team as status questions, and real requests as work?
    Reports the cheap pattern check on its own and then the full pipeline (patterns, then the LLM planner)."""
    rows, regex_hits, full_hits, positives, false_pos, negatives = [], 0, 0, 0, 0, 0
    for c in cases:
        text, expect = c["text"], c["expect"]
        by_regex = bool(agents._STATUS_Q.search(text)) and not router.delegated_targets(text, workers)
        tasks, via = await agents.make_plan(text, workers, user, head_id, {})
        if expect == "status":
            positives += 1
            regex_hits += by_regex
            ok = not tasks  # not handed to a worker: the Head answers from live status
            full_hits += ok
        elif expect == "delegate":
            negatives += 1
            ok = bool(tasks)
            false_pos += not ok
        else:
            negatives += 1
            ok = not tasks
            false_pos += not ok
        rows.append({"id": f"intent-{len(rows) + 1}", "category": "intent", "passed": ok, "checks": [{"name": f"'{text[:48]}' -> {via}, {len(tasks)} task(s), regex={by_regex}", "ok": ok, "detail": f"expected {expect}"}]})
    return {"rows": rows, "regex_recall": round(regex_hits / max(1, positives), 3), "recall": round(full_hits / max(1, positives), 3), "false_positive_rate": round(false_pos / max(1, negatives), 3)}


async def judge_persona(character: dict, message: str, reply: str) -> dict:
    from pydantic import BaseModel, Field

    class Score(BaseModel):
        score: int = Field(ge=1, le=5, description="1 = does not fit the character, 5 = perfectly in character")
        reason: str

    sys = f"Rate whether this reply fits the character: {character['persona']}\nBe strict. Score 1-5."
    s = await ainvoke_retry(get_llm(tier="smart", schema=Score), [("system", sys), ("human", f"User said: {message}\nReply: {reply}")])
    return {"score": s.score, "reason": s.reason}


EVAL_DOC = """Eval Handbook

Parking permits cost $45 per month for employees. The office wifi password rotates every Thursday.
The annual offsite is held in Lisbon every March.
"""


async def run_suite(routing_only: bool = False, limit: int | None = None, judge: bool = False, progress=None, only: str | None = None) -> dict:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    chars = agents.load_characters()
    workers = {k: v for k, v in chars.items() if v.get("role") != "head"}
    started = time.time()

    status_metrics: dict = {}
    routing = run_routing(workers, cases["routing"])
    rows = routing["rows"]
    user = f"eval-{int(started)}"

    if not routing_only:
        import feedback

        live = [c for c in cases["live"] + feedback.eval_cases() if c["agent"] in chars and (not only or c["id"].startswith(only))]  # plus tests made from thumbs-down feedback
        live = live[:limit] if limit else live
        if any(c["id"].startswith("rag") for c in live):  # seed the document the RAG cases ask about
            import knowledge

            await knowledge.ingest(user, "eval-handbook.txt", EVAL_DOC.encode())
        sem = asyncio.Semaphore(2)

        async def one(i: int, c: dict) -> dict:
            async with sem:
                t0 = time.time()
                got = await collect(chars[c["agent"]], c["message"], user)
                row = {"id": c["id"], "category": c["id"].split("-")[0], "checks": check(c, got, user), "latency_ms": round((time.time() - t0) * 1000), "reply": got["reply"][:240], "tools": got["tools"]}
                row["passed"] = all(x["ok"] for x in row["checks"])
                if judge and c["id"].startswith("persona") and not got["error"]:
                    try:
                        row["judge"] = await judge_persona(chars[c["agent"]], c["message"], got["reply"])
                        row["passed"] = row["passed"] and row["judge"]["score"] >= 3
                    except Exception as e:  # noqa: BLE001
                        row["judge"] = {"score": None, "reason": f"judge failed: {type(e).__name__}"}
                if progress:
                    progress(i + 1, len(live))
                return row

        rows += await asyncio.gather(*(one(i, c) for i, c in enumerate(live)))
        if (not only or only == "intent") and cases.get("status_intent"):
            intent = await run_status_intent(workers, cases["status_intent"], "michael_scott", user)
            rows += intent["rows"]
            status_metrics = {k: intent[k] for k in ("regex_recall", "recall", "false_positive_rate")}

    by_cat: dict[str, list[bool]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    report = {
        "started": started,
        "duration_s": round(time.time() - started, 1),
        "total": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "routing_precision": routing["precision"],
        "routing_coverage": routing["coverage"],
        "status_intent": status_metrics,
        "by_category": {k: {"passed": sum(v), "total": len(v)} for k, v in by_cat.items()},
        "cases": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{int(started)}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def latest() -> dict | None:
    f = OUT_DIR / "latest.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _print(report: dict) -> None:
    print(f"\n{report['passed']}/{report['total']} passed in {report['duration_s']}s | router precision {report['routing_precision']:.0%}, coverage {report['routing_coverage']:.0%}")
    if report.get("status_intent"):
        si = report["status_intent"]
        print(f"  status questions: patterns alone catch {si['regex_recall']:.0%}; full pipeline {si['recall']:.0%}; wrongly treated as status: {si['false_positive_rate']:.0%}")
    for cat, v in report["by_category"].items():
        print(f"  {cat:12s} {v['passed']}/{v['total']}")
    for r in report["cases"]:
        if not r["passed"]:
            bad = "; ".join(f"{c['name']} ({c['detail']})" for c in r["checks"] if not c["ok"])
            print(f"  FAIL {r['id']}: {bad}")


if __name__ == "__main__":
    args = sys.argv[1:]
    lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None

    async def main() -> None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(str(db.CHECKPOINT_PATH)) as saver:
            agents.set_checkpointer(saver)
            only = args[args.index("--only") + 1] if "--only" in args else None
            _print(await run_suite("--routing-only" in args, lim, "--judge" in args, lambda i, n: print(f"  live case {i}/{n}", flush=True), only))

    asyncio.run(main())
