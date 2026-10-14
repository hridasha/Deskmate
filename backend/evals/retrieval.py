"""Retrieval benchmark: how well does search find the right passage, and where should the similarity cutoff be?

  python -m evals.retrieval --build     # generate a realistic corpus + paraphrased questions with the LLM (once; saved to disk)
  python -m evals.retrieval             # index it with real embeddings and measure keyword, semantic and hybrid search

Relevance: a retrieved passage is correct if it contains the question's expected answer text.
Unanswerable questions measure how often search returns something when it should stay empty.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import chunking
import embeddings
import knowledge
import llm
import memory
import rerank
from pydantic import BaseModel, Field

CORPUS = Path(__file__).parent / "retrieval_corpus.json"
REPORT = Path(__file__).parent / "retrieval_report.json"
USER = "bench-retrieval"

TOPICS = [
    "annual leave and public holidays", "remote and hybrid work", "travel and expense reimbursement", "information security basics", "onboarding for new hires",
    "company laptops and equipment", "health and wellness benefits", "pension and retirement plan", "performance reviews and promotions", "parental leave",
    "procurement and vendor approval", "code review and release process", "incident response on-call rota", "customer refund policy", "warranty terms for bicycles",
    "warehouse safety rules", "office kitchen and cleaning", "visitor and building access", "data retention and privacy", "training and learning budget",
    "company car and bike-to-work scheme", "overtime and shift allowances", "diversity and inclusion commitments", "whistleblowing and ethics", "quarterly budget planning",
    "supplier payment terms", "social media guidelines", "workplace harassment procedure", "recruitment and referral bonus", "retail store opening hours",
    "shipping and delivery times", "product recall procedure", "sustainability targets", "internal chat and meeting etiquette", "password and device policy",
    "volunteering days", "relocation support", "sick leave procedure", "intellectual property and patents", "end of employment and notice periods",
]


class QA(BaseModel):
    question: str = Field(description="a question a user might ask, worded differently from the document (paraphrase, no copied phrases)")
    answer: str = Field(description="a short exact string (a number, name or date) that appears verbatim in the document and answers the question")


class Doc(BaseModel):
    title: str
    body: str = Field(description="about 350 words of internal policy text with at least six specific, verifiable facts (numbers, names, dates)")
    questions: list[QA] = Field(description="exactly 3 questions")


class Batch(BaseModel):
    docs: list[Doc]


class Unanswerable(BaseModel):
    questions: list[str] = Field(description="20 plausible questions an employee of a bicycle company might ask that none of the policy documents would answer, e.g. about unrelated trivia or other companies")


def _save(data: dict) -> None:
    CORPUS.write_text(json.dumps(data, indent=1), encoding="utf-8")


async def build(per_call: int = 4) -> None:
    """Generate the corpus a few documents per request (free-tier friendly). Safe to re-run: it only fills what is missing."""
    data = json.loads(CORPUS.read_text(encoding="utf-8")) if CORPUS.exists() else {"docs": [], "unanswerable": []}
    done = {d["topic"] for d in data["docs"]}
    todo = [t for t in TOPICS if t not in done]
    for i in range(0, len(todo), per_call):
        group = todo[i : i + per_call]
        prompt = (
            "For the fictional company 'Aurora Cycles', write one internal policy document for EACH of these topics: "
            + "; ".join(group)
            + ". Each body has about 300 words with at least six specific, verifiable facts (numbers, names, dates). "
            "For each document write exactly 3 questions, worded differently from the text (paraphrased), with a short exact answer that appears verbatim in that document's body."
        )
        try:
            batch = await llm.ainvoke_retry(llm.get_llm(tier="fast", schema=Batch), [("human", prompt)])
        except Exception as e:  # noqa: BLE001
            print(f"  stopped after {len(data['docs'])} documents: {type(e).__name__}: {str(e)[:120]}")
            break
        for topic, d in zip(group, batch.docs):
            good = [q.model_dump() for q in d.questions if q.answer.lower() in d.body.lower()]  # keep only answers truly in the text
            if good:
                data["docs"].append({"topic": topic, "title": d.title, "body": d.body, "questions": good})
        _save(data)
        print(f"  {len(data['docs'])}/{len(TOPICS)} documents")
    if not data["unanswerable"]:
        try:
            un = await llm.ainvoke_retry(llm.get_llm(tier="fast", schema=Unanswerable), [("human", "Write 20 questions that would NOT be answered by internal policy documents of a bicycle company (trivia, other companies, personal matters, unrelated technical questions).")])
            data["unanswerable"] = un.questions
            _save(data)
        except Exception as e:  # noqa: BLE001
            print(f"  couldn't write the unanswerable questions yet: {type(e).__name__}")
    print(f"corpus: {len(data['docs'])} documents, {sum(len(d['questions']) for d in data['docs'])} answerable and {len(data['unanswerable'])} unanswerable questions")


BUDGETS = (600, 1500)


async def _index(docs: list[dict], cfg: chunking.Config, cache: dict) -> list[dict]:
    """Chunk every document with `cfg` and embed the passages (each distinct text once)."""
    rows = []
    for d in docs:
        chunks, _ = await chunking.build_chunks([knowledge.Segment(d["body"], "text")], cfg, "txt")
        rows += [{"doc": d["title"], "text": t, "toks": knowledge.tokens(t)} for t, _ in chunks]
    missing = [t for t in dict.fromkeys(r["text"] for r in rows) if t not in cache]
    for t, v in zip(missing, await embeddings.embed_documents(missing)):
        cache[t] = v
    for r in rows:
        r["emb"] = cache[r["text"]]
    return rows


def _orders(rows: list[dict], question: str, qvec) -> tuple[dict[str, list[int]], list[float]]:
    kw = knowledge.bm25(knowledge.tokens(question), rows)
    cos = [memory.cosine(qvec, r["emb"]) if qvec and r["emb"] else 0.0 for r in rows]
    by_kw = [i for i in sorted(range(len(rows)), key=lambda i: kw[i], reverse=True) if kw[i] > 0]
    by_cos = sorted(range(len(rows)), key=lambda i: cos[i], reverse=True)
    fused: dict[int, float] = {}
    for ranked in (by_kw, by_cos):
        for rank, i in enumerate(ranked):
            fused[i] = fused.get(i, 0) + 1 / (60 + rank)
    return {"keyword": by_kw, "semantic": by_cos, "hybrid": sorted(fused, key=fused.get, reverse=True)}, cos


def _found(rows: list[dict], order: list[int], answer: str, *, k: int | None = None, budget: int | None = None) -> bool:
    """Is the answer in the top-k passages, or in the passages that fit a character budget (a passage counts for what fits)?"""
    a, used = answer.lower(), 0
    for n, i in enumerate(order):
        if k is not None and n >= k:
            break
        text = rows[i]["text"]
        if budget is not None:
            if used >= budget:
                break
            text, used = text[: budget - used], used + min(len(text), budget - used)
        if a in text.lower():
            return True
    return False


async def evaluate(sweep_chunking: bool = True) -> dict:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    docs = data["docs"]
    answerable = [(q["question"], q["answer"]) for d in docs for q in d["questions"]]
    unanswerable = data["unanswerable"]
    questions = [q for q, _ in answerable] + unanswerable
    qvecs = await embeddings.embed_queries(questions)
    cache: dict = {}
    report: dict = {"embedding_model": embeddings.model_id(), "docs": len(docs), "answerable": len(answerable), "unanswerable": len(unanswerable)}

    # 1. Search modes, with the production chunking (auto)
    t0 = time.time()
    rows = await _index(docs, chunking.Config("auto"), cache)
    report["passages"] = len(rows)
    report["index_seconds"] = round(time.time() - t0, 1)
    modes = ["keyword", "semantic", "hybrid"]
    try:  # the cross-encoder is optional: skipped if its model can't be loaded
        await asyncio.to_thread(rerank.score_sync, "warm up", ["warm up"])
        modes.append("hybrid+rerank")
    except Exception as e:  # noqa: BLE001
        print(f"  (reranker skipped: {type(e).__name__})")
    scores = {m: {"recall@1": 0, "recall@3": 0, "recall@5": 0, "hit@600": 0, "hit@1500": 0} for m in modes}
    best_cos = []
    for qi, (question, answer) in enumerate(answerable):
        orders, cos = _orders(rows, question, qvecs[qi])
        if "hybrid+rerank" in scores:
            head = orders["hybrid"][: rerank.CANDIDATES]
            rs = await asyncio.to_thread(rerank.score_sync, question, [rows[i]["text"] for i in head])
            orders["hybrid+rerank"] = [i for _, i in sorted(zip(rs, head), key=lambda p: p[0], reverse=True)]
        best_cos.append(max(cos))
        for m, order in orders.items():
            for k in (1, 3, 5):
                scores[m][f"recall@{k}"] += _found(rows, order, answer, k=k)
            for b in BUDGETS:
                scores[m][f"hit@{b}"] += _found(rows, order, answer, budget=b)
    n = len(answerable)
    report["modes"] = {m: {k: round(v / n, 3) for k, v in s.items()} for m, s in scores.items()}

    # 2. The similarity cutoff: keep answerable questions, reject unanswerable ones
    junk_cos = [max(_orders(rows, q, qvecs[len(answerable) + j])[1]) for j, q in enumerate(unanswerable)]
    sweep = []
    for th in [round(x * 0.05, 2) for x in range(6, 17)]:
        kept = sum(c >= th for c in best_cos) / len(best_cos)
        wrong = sum(c >= th for c in junk_cos) / len(junk_cos)
        sweep.append({"cutoff": th, "answerable_kept": round(kept, 3), "unanswerable_wrongly_kept": round(wrong, 3), "balanced": round((kept + (1 - wrong)) / 2, 3)})
    report["cutoff_sweep"] = sweep
    report["best_cutoff"] = max(sweep, key=lambda r: (r["balanced"], -r["cutoff"]))["cutoff"]

    # 3. Chunking: every method gets the same amount of text to retrieve
    if sweep_chunking:
        table = []
        for cfg in [chunking.Config("fixed", 500, 62), chunking.Config("fixed", 900, 112), chunking.Config("fixed", 1400, 175), chunking.Config("sentence", 500, 62), chunking.Config("sentence", 900, 112), chunking.Config("paragraph", 900, 112), chunking.Config("semantic", 900, 112), chunking.Config("auto")]:
            crows = await _index(docs, cfg, cache)
            h600 = h1500 = 0
            for qi, (question, answer) in enumerate(answerable):
                order = _orders(crows, question, qvecs[qi])[0]["hybrid"]
                h600 += _found(crows, order, answer, budget=600)
                h1500 += _found(crows, order, answer, budget=1500)
            table.append({"method": f"{cfg.strategy}" + ("" if cfg.strategy == "auto" else f"/{cfg.size}"), "passages": len(crows), "hit@600": round(h600 / n, 3), "hit@1500": round(h1500 / n, 3)})
        report["chunking"] = table
    if "chunking" not in report and REPORT.exists():  # a quick run keeps the last full chunking table
        try:
            report["chunking"] = json.loads(REPORT.read_text(encoding="utf-8")).get("chunking")
        except (json.JSONDecodeError, OSError):
            pass
    if report.get("chunking") is None:
        report.pop("chunking", None)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    async def main() -> None:
        if "--build" in sys.argv:
            await build()
            return
        rep = await evaluate(sweep_chunking="--no-chunking" not in sys.argv)
        print(f"\nmodel {rep['embedding_model']}: {rep['answerable']} answerable + {rep['unanswerable']} unanswerable questions over {rep['passages']} passages (indexed in {rep['index_seconds']}s)")
        print("\n  search      recall@1  recall@3  recall@5   hit@600  hit@1500")
        for m, v in rep["modes"].items():
            print(f"  {m:13s} {v['recall@1']:6.0%}    {v['recall@3']:6.0%}    {v['recall@5']:6.0%}    {v['hit@600']:6.0%}   {v['hit@1500']:6.0%}")
        print("\n  cutoff  answerable kept  unanswerable wrongly kept  balanced")
        for s in rep["cutoff_sweep"]:
            print(f"   {s['cutoff']:.2f}     {s['answerable_kept']:6.0%}           {s['unanswerable_wrongly_kept']:6.0%}            {s['balanced']:.2f}")
        print(f"  best cutoff: {rep['best_cutoff']}")
        if "chunking" in rep:
            print("\n  chunking     passages  hit@600  hit@1500   (hybrid search)")
            for r in rep["chunking"]:
                print(f"  {r['method']:12s} {r['passages']:6d}   {r['hit@600']:6.0%}   {r['hit@1500']:6.0%}")

    asyncio.run(main())
