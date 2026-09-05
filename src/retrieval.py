"""Local retrieval over the committed policy index.

Reads index/chunks.json + index/embeddings.npy + index/index_meta.json. The
embedding method is recorded at build time and must match at runtime so the
vector dimensions always align. Keyword retrieval is always available as a
fallback.
"""
from __future__ import annotations
import json
from typing import Any

import numpy as np

from src.config import INDEX_DIR, log
from src.utils import text_hash, cosine, jaccard_tokens, safe_float


class IndexNotReady(Exception):
    pass


_meta: dict | None = None
_chunks: list[dict] | None = None
_matrix: np.ndarray | None = None


def _load() -> None:
    global _meta, _chunks, _matrix
    if _meta is not None:
        return
    chunks_path = INDEX_DIR / "chunks.json"
    npy_path = INDEX_DIR / "embeddings.npy"
    meta_path = INDEX_DIR / "index_meta.json"
    if not (chunks_path.exists() and npy_path.exists() and meta_path.exists()):
        raise IndexNotReady("Policy index missing - run scripts/build_index.py")
    with meta_path.open("r", encoding="utf-8") as f:
        _meta = json.load(f)
    with chunks_path.open("r", encoding="utf-8") as f:
        _chunks = json.load(f)
    _matrix = np.load(str(npy_path))
    if len(_chunks) != _matrix.shape[0]:
        log.warning("index chunk/embedding count mismatch: %d vs %d",
                    len(_chunks), _matrix.shape[0])


def meta() -> dict:
    _load()
    return _meta


def chunk_count() -> int:
    _load()
    return len(_chunks or [])


def embedding_method() -> str:
    _load()
    return _meta.get("embedding_method", "text_hash")


def _embed(text: str) -> np.ndarray:
    method = embedding_method()
    if method == "text_hash":
        return np.asarray(text_hash(text), dtype=np.float32)
    if method.startswith("gemini"):
        try:
            from src.gemini_client import embed_text
            return np.asarray(embed_text(text), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini embedding failed (%s); using text_hash", exc)
    return np.asarray(text_hash(text), dtype=np.float32)


def search(query: str, top_k: int = 6) -> list[dict]:
    """Retrieve the most relevant policy chunks for a query."""
    _load()
    q = query.lower().strip()
    vec = _embed(query)

    vec_dim = _matrix.shape[1]
    query_score = None
    if vec.shape[0] == vec_dim:
        query_score = _matrix @ vec
    else:
        log.warning("Matrix dim %d != query dim %d; keyword-only fallback", vec_dim, vec.shape[0])

    scored = []
    for i, chunk in enumerate(_chunks):
        kw = jaccard_tokens(q, chunk["text"])
        emb = safe_float(query_score[i]) if query_score is not None else 0.0
        score = 0.65 * kw + 0.35 * emb if query_score is not None else kw
        if score > 0:
            scored.append((score, i, chunk))

    scored.sort(key=lambda x: -x[0])
    results = []
    for score, i, chunk in scored[:top_k]:
        results.append({
            "score": round(score, 3),
            "chunk_id": chunk.get("chunk_id", str(i)),
            "title": chunk.get("title", "Policy document"),
            "section": chunk.get("section", ""),
            "source": chunk.get("source", ""),
            "text": chunk["text"],
        })
    return results


def context(query: str, top_k: int = 6) -> list[str]:
    results = search(query, top_k=top_k)
    return [f"[{r['source']} | {r['section']}] {r['text']}" for r in results] if results else []