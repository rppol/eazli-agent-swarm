"""Load the parsed amazon.sa catalog into the `products` Chroma collection.

Items that failed parsing are loaded too, not dropped. An agent needs to be
able to find a product and then say "I cannot confirm this fits" — silently
hiding the messy half of the catalog would make the system look more capable
than it is, which is exactly the failure mode this project is arguing against.
"""

from __future__ import annotations

import json
from collections import Counter

import chromadb
from chromadb.utils import embedding_functions

from app.catalog import parse_capture

CHROMA_DIR = "data/chroma"
EMBED_MODEL = "all-MiniLM-L6-v2"


def main() -> None:
    products = parse_capture()

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection("products")
    except Exception:
        pass

    collection = client.create_collection(
        name="products",
        embedding_function=embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        ),
    )

    # Embed title plus category and style so a query like "warm minimal sofa"
    # matches on intent, not only on literal title words.
    documents = [
        f"{p.title}. Category: {p.category}. Style: {', '.join(p.style_tags) or 'unspecified'}."
        for p in products
    ]

    collection.add(
        ids=[p.asin for p in products],
        documents=documents,
        metadatas=[p.to_metadata() for p in products],
    )

    conf = Counter(p.dims_confidence for p in products)
    flags = Counter(f for p in products for f in p.flags)
    print(f"products          {len(products):>4} items")
    print(f"  usable          {sum(p.usable for p in products):>4}")
    print(f"  confidence      {json.dumps(dict(conf))}")
    print(f"  flags           {json.dumps(dict(flags))}")
    print(f"persisted to {CHROMA_DIR}/")


if __name__ == "__main__":
    main()
