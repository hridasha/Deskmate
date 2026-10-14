"""Held-out check for the cheap, offline decision layers (router and status patterns).

heldout.json was written AFTER the router and the patterns were tuned, and none of it was used to tune them, so these numbers are a
more honest estimate than the main suite's. Nothing here calls an LLM: where the router isn't confident it defers to the LLM planner,
which is counted as "deferred" (acceptable), not as a miss.

  python -m evals.heldout
"""
import json
from pathlib import Path

import agent as agents
import router

FILE = Path(__file__).parent / "heldout.json"


def run() -> dict:
    cases = json.loads(FILE.read_text(encoding="utf-8"))
    workers = {k: v for k, v in agents.load_characters().items() if v.get("role") != "head" and not v.get("custom")}  # the built-in cast only: custom hires change the answer
    rt = router.Router(workers)
    confident = correct = answerable = deferred_ok = ambiguous = ambiguous_deferred = 0
    wrong = []
    for c in cases["routing"]:
        scores = rt.scores(c["text"])
        top, conf = scores[0][0], rt.confident(scores)
        if c["expect"] is None:
            ambiguous += 1
            ambiguous_deferred += not conf
            if conf:
                wrong.append(f"{c['text']!r}: should defer but chose {top}")
        else:
            answerable += 1
            if conf:
                confident += 1
                correct += top == c["expect"]
                if top != c["expect"]:
                    wrong.append(f"{c['text']!r}: chose {top}, expected {c['expect']}")
            else:
                deferred_ok += 1
    pos = hit = neg = fp = 0
    status_misses = []
    for c in cases["status_intent"]:
        by_regex = bool(agents._STATUS_Q.search(c["text"])) and not router.delegated_targets(c["text"], workers)
        if c["expect"] == "status":
            pos += 1
            hit += by_regex
            if not by_regex:
                status_misses.append(c["text"])
        else:
            neg += 1
            fp += by_regex
            if by_regex:
                status_misses.append(f"false alarm: {c['text']}")
    return {
        "routing_cases": len(cases["routing"]),
        "router_precision_when_confident": round(correct / max(1, confident), 3),
        "router_confident_on": f"{confident}/{answerable}",
        "router_deferred_on_clear_cases": deferred_ok,
        "ambiguous_correctly_deferred": f"{ambiguous_deferred}/{ambiguous}",
        "status_pattern_recall": round(hit / max(1, pos), 3),
        "status_false_alarms": f"{fp}/{neg}",
        "routing_errors": wrong,
        "status_errors": status_misses,
    }


if __name__ == "__main__":
    rep = run()
    for k, v in rep.items():
        if isinstance(v, list):
            print(f"\n{k}:")
            for line in v:
                print("  -", line)
        else:
            print(f"{k}: {v}")
