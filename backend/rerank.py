"""Optional cross-encoder reranking of search results.

Hybrid search (keywords + embeddings) is fast but only compares the question and each passage separately. A cross-encoder reads
the question and the passage together, which is slower and more accurate, so it is applied to the top candidates only.

  RERANKER=auto|on|off|<model name>   auto (default) = on whenever local embeddings are in use. Measured on the bundled benchmark
                                      (python -m evals.retrieval): top-1 hit rate 74% -> 88%, answer found within 600 characters 79% -> 88%
  RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
"""
import asyncio
import os
import threading

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CANDIDATES = 15  # how many fused results are re-scored
_model = None
_lock = threading.Lock()


def model_name() -> str | None:
    choice = os.getenv("RERANKER", "auto").strip()
    if choice.lower() in ("off", "0", "false", "no"):
        return None
    if choice.lower() in ("", "auto"):
        import embeddings

        return os.getenv("RERANK_MODEL", DEFAULT_MODEL) if embeddings.provider() == "local" else None
    return os.getenv("RERANK_MODEL", DEFAULT_MODEL) if choice.lower() in ("on", "1", "true", "yes") else choice


def _load(name: str):
    global _model
    with _lock:
        if _model is None or _model[0] != name:
            from sentence_transformers import CrossEncoder

            _model = (name, CrossEncoder(name, max_length=384))
        return _model[1]


def score_sync(query: str, passages: list[str], name: str | None = None) -> list[float]:
    model = _load(name or model_name() or DEFAULT_MODEL)
    return [float(s) for s in model.predict([(query, p) for p in passages], show_progress_bar=False)]


async def rerank(query: str, passages: list[str]) -> list[float] | None:
    """Relevance scores for each passage, or None when reranking is off or unavailable (callers keep the fused order)."""
    name = model_name()
    if not name or not passages:
        return None
    try:
        return await asyncio.to_thread(score_sync, query, passages, name)
    except Exception as e:  # noqa: BLE001  a missing model or no network must never break search
        print(f"Reranker unavailable ({type(e).__name__}); using the fused order")
        return None
