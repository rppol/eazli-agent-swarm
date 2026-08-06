"""Build the ChromaDB collections the agents retrieve from.

Three collections, kept separate because they answer different questions and
get filtered differently:

    eazli_kb           what eazli says about itself: FAQs, policies, plans
    design_principles  the spatial rules geometry.py enforces, so Noura can
                       cite *why* a layout works rather than only asserting it
    products           the amazon.sa catalog (loaded separately by the parser)

Embeddings run locally via all-MiniLM-L6-v2. Nothing leaves the machine, which
keeps the "no LLM APIs" constraint true at query time as well as build time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

KB_RAW = Path("kb/raw")
CHROMA_DIR = "data/chroma"
EMBED_MODEL = "all-MiniLM-L6-v2"


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=CHROMA_DIR)


def _embedder():
    return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)


def _faq_docs() -> list[dict]:
    rows = json.loads((KB_RAW / "faq.json").read_text(encoding="utf-8"))
    docs = []
    for i, row in enumerate(rows):
        docs.append(
            {
                "id": f"faq-{i:03d}",
                # Embed question and answer together: users ask in the shape of
                # the question, but the answer carries the retrievable detail.
                "text": f"{row['q']}\n{row['a']}",
                "meta": {
                    "source_url": "https://www.eazli.com/faq",
                    "doc_type": "faq",
                    "audience": row["cat"],
                    "topic": row["topic"],
                    "question": row["q"],
                    "agent": "",
                },
            }
        )
    return docs


def _page_docs() -> list[dict]:
    rows = json.loads((KB_RAW / "pages.json").read_text(encoding="utf-8"))
    return [
        {
            "id": f"page-{i:03d}",
            "text": f"{row['section']}\n{row['text']}",
            "meta": {
                "source_url": row["source_url"],
                "doc_type": row["doc_type"],
                "audience": "common",
                "topic": row["section"],
                "question": "",
                "agent": row.get("agent") or "",
            },
        }
        for i, row in enumerate(rows)
    ]


# Headings in policy-ai-agent-scope.md that are our commentary, not eazli's text.
OUR_ANALYSIS_HEADINGS = {
    "Constraints this imposes on the prototype",
    "The access-path gap",
}


def _policy_docs() -> list[dict]:
    """Split the AI Agent scope policy on its markdown headings.

    Section-level chunks keep each clause whole. Splitting mid-clause would let
    an agent retrieve half of a limitation, which is worse than retrieving none.
    """
    path = KB_RAW / "policy-ai-agent-scope.md"
    text = path.read_text(encoding="utf-8")
    chunks = re.split(r"\n(?=#{2,3} )", text)

    docs = []
    for i, chunk in enumerate(chunks):
        body = chunk.strip()
        if len(body) < 80:
            continue
        heading = body.splitlines()[0].lstrip("# ").strip()
        agent = ""
        for name in ("zeina", "noura", "adam"):
            if name in heading.lower():
                agent = name

        # The file also carries our own commentary on the policy. That must not
        # be retrievable as though eazli published it — an agent citing our
        # analysis as their stated position would be inventing a source.
        is_ours = heading in OUR_ANALYSIS_HEADINGS
        docs.append(
            {
                "id": f"policy-ai-agent-{i:03d}",
                "text": body,
                "meta": {
                    "source_url": (
                        "docs/analysis — not published by eazli"
                        if is_ours
                        else "https://www.eazli.com/terms-ai-agent"
                    ),
                    "doc_type": "analysis" if is_ours else "policy",
                    "audience": "common",
                    "topic": heading,
                    "question": "",
                    "agent": agent,
                },
            }
        )
    return docs


def _principle_docs() -> list[dict]:
    from app.geometry import RULES

    return [
        {
            "id": f"rule-{key}",
            "text": f"{key.replace('_', ' ')}: {rule['text']}",
            "meta": {
                "rule": key,
                "value_cm": rule.get("value_cm", 0),
                "enforced_by": "app/geometry.py",
            },
        }
        for key, rule in RULES.items()
    ]


def _load(client, name: str, docs: list[dict]) -> int:
    if not docs:
        return 0
    try:
        client.delete_collection(name)
    except Exception:
        pass
    collection = client.create_collection(name=name, embedding_function=_embedder())
    collection.add(
        ids=[d["id"] for d in docs],
        documents=[d["text"] for d in docs],
        metadatas=[d["meta"] for d in docs],
    )
    return len(docs)


def main() -> None:
    client = _client()

    kb_docs = _faq_docs() + _page_docs() + _policy_docs()
    n_kb = _load(client, "eazli_kb", kb_docs)
    n_rules = _load(client, "design_principles", _principle_docs())

    print(f"eazli_kb          {n_kb:>4} chunks")
    print(f"design_principles {n_rules:>4} chunks")
    print(f"persisted to {CHROMA_DIR}/")


if __name__ == "__main__":
    main()
