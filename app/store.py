"""ChromaDB access. One place that knows how retrieval is spelled.

The embedding model is loaded once per process and cached: it is ~80MB and
loading it per request would dominate latency.
"""

from __future__ import annotations

from functools import lru_cache

import chromadb
from chromadb.utils import embedding_functions

CHROMA_DIR = "data/chroma"
EMBED_MODEL = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _embedder():
    return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)


@lru_cache(maxsize=1)
def _client(path: str = CHROMA_DIR):
    return chromadb.PersistentClient(path=path)


@lru_cache(maxsize=8)
def collection(name: str, path: str = CHROMA_DIR):
    return _client(path).get_collection(name=name, embedding_function=_embedder())


def has_collection(name: str, path: str = CHROMA_DIR) -> bool:
    try:
        collection(name, path)
        return True
    except Exception:
        return False


def search(
    name: str,
    query: str,
    k: int = 5,
    where: dict | None = None,
) -> list[dict]:
    """Semantic search returning flat dicts with metadata merged in.

    Chroma returns distances (lower is closer); we expose `score` as a
    similarity so callers can rank without knowing that convention.
    """
    if not has_collection(name):
        return []

    res = collection(name).query(
        query_texts=[query],
        n_results=k,
        where=where or None,
    )
    if not res["ids"] or not res["ids"][0]:
        return []

    out = []
    for i, doc_id in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i] or {}
        distance = res["distances"][0][i] if res.get("distances") else 0.0
        out.append({"id": doc_id, "text": res["documents"][0][i], "score": round(1 - distance, 4), **meta})
    return out
