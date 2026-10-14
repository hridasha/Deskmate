"""Cross-encoder reranking plumbing (the real model is exercised by `python -m evals.retrieval`)."""
import pytest

import knowledge
import memory
import rerank


@pytest.fixture(autouse=True)
def fresh(clean_db, monkeypatch):
    knowledge._cache.clear()
    knowledge._version.clear()

    async def none(texts):
        return [None] * len(texts)

    async def one(text):
        return None

    monkeypatch.setattr(knowledge, "embed_many", none)
    monkeypatch.setattr(memory, "embed", one)


def test_it_is_off_without_local_embeddings_and_can_be_forced(monkeypatch):
    monkeypatch.delenv("RERANKER", raising=False)
    assert rerank.model_name() is None  # the test environment has EMBEDDINGS=none
    monkeypatch.setenv("RERANKER", "on")
    assert rerank.model_name() == rerank.DEFAULT_MODEL
    monkeypatch.setenv("RERANKER", "some/other-model")
    assert rerank.model_name() == "some/other-model"
    monkeypatch.setenv("RERANKER", "off")
    assert rerank.model_name() is None


@pytest.mark.asyncio
async def test_search_uses_the_cross_encoder_order(monkeypatch):
    docs = {
        "a.txt": b"Parking permits cost 45 dollars per month for staff and are renewed yearly by reception.",
        "b.txt": b"The parking lot closes at night. Permits are checked by security staff each morning at the gate.",
    }
    for name, data in docs.items():
        await knowledge.ingest("u1", name, data)
    monkeypatch.setenv("RERANKER", "on")
    seen = {}

    def fake_scores(query, passages, name=None):
        seen["n"] = len(passages)
        return [1.0 if "closes at night" in p else 0.0 for p in passages]  # the cross-encoder prefers b.txt

    monkeypatch.setattr(rerank, "score_sync", fake_scores)
    hits = await knowledge.search("u1", "x", "parking permit", k=2)
    assert seen["n"] == 2 and hits[0]["doc"] == "b.txt"
    monkeypatch.setenv("RERANKER", "off")
    plain = await knowledge.search("u1", "x", "parking permit", k=2)
    assert plain[0]["doc"] == "a.txt"  # without it, the keyword-fused order stands


@pytest.mark.asyncio
async def test_a_broken_reranker_falls_back_to_the_fused_order(monkeypatch):
    await knowledge.ingest("u1", "a.txt", b"Parking permits cost 45 dollars per month for staff and are renewed yearly by reception.")
    await knowledge.ingest("u1", "b.txt", b"Visitors park in the north lot and sign in with reception before 9am each day.")
    monkeypatch.setenv("RERANKER", "on")

    def boom(*a, **k):
        raise OSError("model download failed")

    monkeypatch.setattr(rerank, "score_sync", boom)
    hits = await knowledge.search("u1", "x", "parking permit cost", k=2)
    assert hits and hits[0]["doc"] == "a.txt"
