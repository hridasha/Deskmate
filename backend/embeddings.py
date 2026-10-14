"""Embeddings behind one interface.

Default: a local sentence-transformers model. It is free, private, works offline and has no daily quota, which matters
because hosted free tiers allow only a few requests per day. Gemini embeddings remain available as an option.

  EMBEDDINGS=auto|local|gemini|none      (auto = local if installed, else Gemini if a key exists)
  EMBED_MODEL_LOCAL=BAAI/bge-small-en-v1.5   any sentence-transformers model (384-d bge-small is a strong small default)

Every stored vector records which model made it, so switching models never mixes incompatible vectors; stale rows are
re-embedded by reindex().
"""
import asyncio
import os
import threading

LOCAL_MODEL = os.getenv("EMBED_MODEL_LOCAL", "BAAI/bge-small-en-v1.5")

# Retrieval models are often trained with an instruction on the query side and/or a marker on the passage side.
_QUERY_PREFIX = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-large-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "intfloat/multilingual-e5-small": "query: ",
    "intfloat/multilingual-e5-base": "query: ",
    "intfloat/e5-small-v2": "query: ",
}
_DOC_PREFIX = {"intfloat/multilingual-e5-small": "passage: ", "intfloat/multilingual-e5-base": "passage: ", "intfloat/e5-small-v2": "passage: "}

_model = None
_model_lock = threading.Lock()


def _local_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def provider() -> str:
    choice = os.getenv("EMBEDDINGS", "auto").lower()
    if choice == "none":
        return "none"
    if choice in ("local", "gemini"):
        return choice
    if _local_available():
        return "local"
    import llm

    return "gemini" if llm.keys_for("gemini") else "none"


def model_id() -> str:
    """Identifies the vector space. Vectors from different ids must never be compared."""
    p = provider()
    if p == "local":
        return f"local:{LOCAL_MODEL}"
    if p == "gemini":
        import memory

        return f"gemini:{memory.EMBED_MODEL}:256"
    return "none"


def _load_local():
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(LOCAL_MODEL, device="cpu")
    return _model


def _encode_local(texts: list[str], prefix: str) -> list[list[float]]:
    model = _load_local()
    vecs = model.encode([prefix + t for t in texts], batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


async def warm_up() -> None:
    """Load the local model at startup so the first search isn't slow (downloads it once if needed)."""
    if provider() == "local":
        try:
            await asyncio.to_thread(_load_local)
        except Exception as e:  # noqa: BLE001 - a failed download must not stop the app
            print(f"Local embedding model unavailable: {e!r}")


async def embed_documents(texts: list[str]) -> list[list[float] | None]:
    if not texts:
        return []
    p = provider()
    try:
        if p == "local":
            return list(await asyncio.to_thread(_encode_local, texts, _DOC_PREFIX.get(LOCAL_MODEL, "")))
        if p == "gemini":
            import llm
            import memory

            out: list[list[float] | None] = []
            for i in range(0, len(texts), 50):
                batch = texts[i : i + 50]
                out.extend(list(v) for v in await llm.with_key("gemini", memory.EMBED_MODEL, lambda key, b=batch: memory._gemini_embedder(key).aembed_documents(b)))
            return out
    except Exception:  # noqa: BLE001 - search degrades to keywords instead of failing
        pass
    return [None] * len(texts)


async def embed_queries(texts: list[str]) -> list[list[float] | None]:
    """Many queries at once (one model call locally); used by the chunking tuner."""
    if not texts:
        return []
    if provider() == "local":
        try:
            return list(await asyncio.to_thread(_encode_local, texts, _QUERY_PREFIX.get(LOCAL_MODEL, "")))
        except Exception:  # noqa: BLE001
            return [None] * len(texts)
    return [await embed_query(t) for t in texts]


async def embed_query(text: str) -> list[float] | None:
    p = provider()
    try:
        if p == "local":
            return (await asyncio.to_thread(_encode_local, [text], _QUERY_PREFIX.get(LOCAL_MODEL, "")))[0]
        if p == "gemini":
            import llm
            import memory

            return list(await llm.with_key("gemini", memory.EMBED_MODEL, lambda key: memory._gemini_embedder(key).aembed_query(text)))
    except Exception:  # noqa: BLE001
        pass
    return None


# Similarity cutoffs differ between models, so each one is calibrated separately (see evals/retrieval.py).
# 'chunk' = a document passage counts as related; 'memory' = a remembered fact counts as related.
DEFAULT_THRESHOLDS = {
    "local:BAAI/bge-small-en-v1.5": {"chunk": 0.60, "memory": 0.60},
    "local:sentence-transformers/all-MiniLM-L6-v2": {"chunk": 0.30, "memory": 0.40},
    "gemini": {"chunk": 0.60, "memory": 0.70},
}


def threshold(kind: str) -> float:
    env = os.getenv(f"EMBED_MIN_{kind.upper()}")
    if env:
        return float(env)
    mid = model_id()
    table = DEFAULT_THRESHOLDS.get(mid) or DEFAULT_THRESHOLDS.get("gemini" if mid.startswith("gemini") else "", {})
    return table.get(kind, 0.55)
