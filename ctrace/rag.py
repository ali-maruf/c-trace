"""C-Trace retrieval: lightweight, offline RAG over the C-Trace knowledge base.

TF-IDF + cosine similarity keeps the demo dependency-free and deterministic. In production,
swap `Retriever` for pgvector embeddings without changing callers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import KNOWLEDGE_DIR


@dataclass
class Chunk:
    title: str
    source: str
    text: str


def load_chunks() -> list[Chunk]:
    raw = (KNOWLEDGE_DIR / "kb.md").read_text(encoding="utf-8")
    chunks = []
    for block in re.split(r"\n## ", raw)[1:]:
        lines = block.strip().splitlines()
        title = lines[0].strip()
        src = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Source:")), "C-Trace")
        body = " ".join(l for l in lines[1:] if not l.startswith("Source:")).strip()
        chunks.append(Chunk(title, src, body))
    return chunks


class Retriever:
    def __init__(self):
        self.chunks = load_chunks()
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.mat = self.vec.fit_transform([f"{c.title} {c.text}" for c in self.chunks])

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        sims = cosine_similarity(self.vec.transform([query]), self.mat)[0]
        order = sims.argsort()[::-1][:k]
        return [(self.chunks[i], float(sims[i])) for i in order if sims[i] > 0]


@lru_cache(maxsize=1)
def retriever() -> Retriever:
    return Retriever()
