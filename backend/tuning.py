"""Data-driven chunking: try several methods and sizes on the document itself and keep the one that retrieves best.

How it measures (no AI calls, so it costs no quota): it picks sentences that state a concrete fact (a number or a name),
removes that fact and some other words to make a "probe question", and then checks, for every candidate chunking, whether
the passages search returns contain the original sentence. Search is the same hybrid (keywords + embeddings) the app really uses.

Fairness: every method gets the same CHARACTER BUDGET of retrieved text (like the context an agent really receives). Counting
"top 3 passages" would reward huge chunks, because fewer, bigger passages are easier to hit. Within a budget, a chunking that
cuts sentences in half, or buries facts in big mixed passages, loses.
"""
import math
import random
import re
from collections import Counter
from dataclasses import dataclass

import chunking
import embeddings
import memory
from router import STOP, tokens

SIZES = (300, 500, 900)  # benchmark: smaller passages win under an equal text budget
BUDGETS = (600, 1500)  # characters of retrieved text: a tight budget and a roomy one
MAX_CHARS = 40_000  # tune on a sample of a very large document
MIN_PROBES = 8
_NUM = re.compile(r"\b\d[\d,.]*\b")


@dataclass
class Probe:
    query: str
    answer: str
    sentence: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def make_probes(segments, n: int = 30, seed: int = 7) -> list[Probe]:
    """Fact-bearing sentences turned into questions with the fact removed."""
    rng = random.Random(seed)
    cands: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()
    for seg in segments:
        for s in chunking.sentences(seg.text):
            if not 40 <= len(s) <= 240 or s in seen:
                continue
            seen.add(s)
            nums = _NUM.findall(s)
            caps = re.findall(r"\b[A-Z][a-z]{3,}\b", s)[1:]  # names, skipping the sentence's first word
            answer = nums[0] if nums else (caps[0] if caps else "")
            if not answer:
                continue
            words = [w for w in re.findall(r"[A-Za-z']+", s) if w.lower() not in STOP and len(w) > 2 and w != answer]
            if len(words) >= 4:
                cands.append((s, answer, words))
    rng.shuffle(cands)
    probes = []
    for s, answer, words in cands[:n]:
        kept = [w for w in words if rng.random() > 0.25] or words  # drop some words so it isn't a copy of the sentence
        probes.append(Probe(" ".join(kept if len(kept) >= 3 else words), answer, s))
    return probes


def candidate_configs(segments, base: chunking.Config) -> list[chunking.Config]:
    locators = [s.locator for s in segments]
    text = "\n\n".join(s.text for s in segments)
    strategies = ["fixed", "sentence", "paragraph"]
    if sum(1 for loc in locators if loc.startswith("section '")) >= 2 or chunking.count_headings(text) >= 2:
        strategies.append("structure")
    if len(segments) >= 2 and all(loc.startswith("p.") for loc in locators):
        strategies.append("page")
    if len(text) >= 6000 and embeddings.provider() != "none":
        strategies.append("semantic")
    return [chunking.Config(st, size, max(0, size // 8)) for st in strategies for size in SIZES]


def _bm25(query: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs)
    df = Counter(t for d in docs for t in set(d))
    avg = (sum(len(d) for d in docs) / n) or 1.0
    out = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(query):
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        out.append(s)
    return out


def _hits(chunk_texts: list[str], chunk_vecs, probe: Probe, qvec) -> dict[int, bool]:
    """For each character budget: do the best-ranked passages that fit in it contain the probe's source sentence?"""
    kw = _bm25(tokens(probe.query), [tokens(t) for t in chunk_texts])
    cos = [memory.cosine(qvec, v) if qvec and v else 0.0 for v in chunk_vecs]
    fused: dict[int, float] = {}
    for scores, keep in ((kw, lambda v: v > 0), (cos, lambda v: v > 0)):
        order = sorted((i for i, v in enumerate(scores) if keep(v)), key=lambda i: scores[i], reverse=True)
        for rank, i in enumerate(order):
            fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
    target = _norm(probe.sentence)
    order = sorted(fused, key=fused.get, reverse=True)
    out = {}
    for budget in BUDGETS:
        used, hit = 0, False
        for i in order:
            room = budget - used
            if room <= 0:
                break
            visible = chunk_texts[i][:room]  # a passage only contributes what fits in the budget, as in a real context window
            used += len(visible)
            if target in _norm(visible):
                hit = True
                break
        out[budget] = hit
    return out


def _sample(segments):
    """Keep the tuning set small for very large documents by taking segments spread evenly across them."""
    total = sum(len(s.text) for s in segments)
    if total <= MAX_CHARS:
        return segments
    step = total / MAX_CHARS
    return [s for i, s in enumerate(segments) if int(i % step) == 0] or segments[:1]


async def tune(segments, kind: str, base: chunking.Config) -> tuple[chunking.Config | None, dict]:
    """Returns the best configuration (or None if there isn't enough text to measure) and a report of every candidate."""
    sample = _sample(segments)
    probes = make_probes(sample)
    if len(probes) < MIN_PROBES:
        return None, {"probes": len(probes), "note": "not enough fact-bearing sentences to measure"}
    qvecs = await embeddings.embed_queries([p.query for p in probes])
    cache: dict[str, list[float] | None] = {}  # many candidates cut identical passages: embed each distinct text once
    results = []
    for cfg in candidate_configs(sample, base):
        chunks, _ = await chunking.build_chunks(sample, cfg, kind, _tuning=False)
        texts = [t for t, _ in chunks]
        if not texts:
            continue
        missing = [t for t in dict.fromkeys(texts) if t not in cache]
        for t, v in zip(missing, await embeddings.embed_documents(missing)):
            cache[t] = v
        vecs = [cache[t] for t in texts]
        hits = [_hits(texts, vecs, p, q) for p, q in zip(probes, qvecs)]
        n = len(hits)
        tight = sum(h[BUDGETS[0]] for h in hits) / n
        roomy = sum(h[BUDGETS[1]] for h in hits) / n
        results.append({"strategy": cfg.strategy, "size": cfg.size, "overlap": cfg.overlap, "chunks": len(texts), f"hit@{BUDGETS[0]}": round(tight, 3), f"hit@{BUDGETS[1]}": round(roomy, 3), "score": round(0.4 * tight + 0.6 * roomy, 4)})
    if not results:
        return None, {"probes": len(probes), "note": "no candidate produced passages"}
    results.sort(key=lambda r: (-r["score"], r["chunks"]))  # tie: fewer passages is cheaper to store and search
    best = results[0]
    return chunking.Config(best["strategy"], best["size"], best["overlap"]), {"probes": len(probes), "tested": len(results), "results": results[:10], "chosen": best}
