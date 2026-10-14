"""Long-term memory per (user, agent): facts the agent chose to remember, retrieved by semantic similarity.

Uses Gemini embeddings when available and falls back to keyword overlap, so it degrades instead of breaking.
"""
import math
import os

import db
import embeddings
import llm
from router import tokens

EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
# The cutoff depends on the embedding model: see embeddings.threshold("memory").


def _gemini_embedder(key: str):
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    try:
        return GoogleGenerativeAIEmbeddings(model=EMBED_MODEL, google_api_key=key, output_dimensionality=256)
    except TypeError:
        return GoogleGenerativeAIEmbeddings(model=EMBED_MODEL, google_api_key=key)


async def embed(text: str) -> list[float] | None:
    """A query vector from the configured embedding model (local by default); None means search by keywords."""
    return await embeddings.embed_query(text)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def keyword_score(query: str, doc: str) -> float:
    q, d = set(tokens(query)), set(tokens(doc))
    return len(q & d) / max(1, len(q)) if q and d else 0.0


def has_memories(user: str, agent: str) -> bool:
    return bool(db.q("SELECT 1 FROM memories WHERE user=? AND agent=? LIMIT 1", (user, agent)))


async def remember(user: str, agent: str, fact: str) -> str:
    fact = " ".join(fact.split())[:500]
    if not fact:
        return "Nothing to remember."
    if any(m["text"].lower() == fact.lower() for m in db.list_memories(user, agent, 100)):
        return "Already remembered."
    vec = await embeddings.embed_documents([fact])
    db.add_memory(user, agent, fact, vec[0], embeddings.model_id())
    return "Remembered."


async def recall(user: str, agent: str, query: str, k: int = 4) -> list[str]:
    mems = db.list_memories(user, agent)
    if not mems:
        return []
    current = embeddings.model_id()
    usable = [m for m in mems if m["emb"] and m["emb_model"] == current]  # vectors from another model aren't comparable
    qv = await embed(query) if usable else None
    scored = []
    for m in mems:
        if qv and m in usable:
            scored.append((cosine(qv, m["emb"]), m["text"], embeddings.threshold("memory")))
        else:
            scored.append((keyword_score(query, m["text"]), m["text"], 0.15))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for score, text, floor in scored[:k] if score >= floor]


async def reindex() -> int:
    """Re-embed memories made with a different (or no) embedding model, so changing models loses nothing."""
    current = embeddings.model_id()
    if current == "none":
        return 0
    rows = db.q("SELECT id, text FROM memories WHERE emb IS NULL OR emb_model IS NULL OR emb_model != ?", (current,))
    done = 0
    for i in range(0, len(rows), 32):
        batch = rows[i : i + 32]
        vecs = await embeddings.embed_documents([r["text"] for r in batch])
        for r, v in zip(batch, vecs):
            if v:
                db.run("UPDATE memories SET emb=?, emb_model=? WHERE id=?", (json.dumps(v), current, r["id"]))
                done += 1
    return done
