"""C-Trace retrieval (RAG v2): hybrid BM25 + TF-IDF over C-Trace's own knowledge-base summaries.

Two lexical rankers with different strengths (BM25: exact terms; TF-IDF bigrams: phrases) are fused with
reciprocal-rank fusion. Every chunk has a stable id (`kb:<slug>`) so answers and reports can cite it. Bangla
questions are mapped onto the English knowledge base through a small term map. Dense multilingual embeddings
are a planned upgrade (DECISIONS D52); callers only see `search()`.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import KNOWLEDGE_DIR

RRF_K = 60
BN_TERMS = {  # Bangla → English retrieval terms (domain words only)
    "স্কোপ": "scope", "নির্গমন": "emissions", "কার্বন": "carbon", "বিদ্যুৎ": "electricity", "গ্রিড": "grid", "গ্যাস": "gas",
    "ডিজেল": "diesel", "ব্যাংক": "bank", "ঋণ": "loan finance", "সবুজ": "green", "শ্রেণিবিন্যাস": "taxonomy", "ক্রেতা": "brand buyer supplier",
    "ব্র্যান্ড": "brand", "প্রতিবেদন": "report disclosure", "সাশ্রয়": "savings reduction", "হ্রাস": "reduction", "যাচাই": "verification",
    "সৌর": "solar", "বয়লার": "boiler", "বিএসইসি": "bsec", "আইএফআরএস": "ifrs", "পাসপোর্ট": "passport", "ইউরোপ": "eu",
}


@dataclass
class Chunk:
    id: str
    title: str
    source: str
    text: str


def slug(title: str) -> str:
    return "kb:" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def load_chunks() -> list[Chunk]:
    raw = (KNOWLEDGE_DIR / "kb.md").read_text(encoding="utf-8")
    chunks = []
    for block in re.split(r"\n## ", raw)[1:]:
        lines = block.strip().splitlines()
        title = lines[0].strip()
        src = next((ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("Source:")), "C-Trace")
        body = " ".join(ln for ln in lines[1:] if not ln.startswith("Source:")).strip()
        chunks.append(Chunk(slug(title), title, src, body))
    return chunks


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in ENGLISH_STOP_WORDS and len(t) > 1]


def translate_query(q: str) -> str:
    extra = [en for bn, en in BN_TERMS.items() if bn in q]
    return f"{q} {' '.join(extra)}".strip()


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avg = sum(map(len, docs)) / max(len(docs), 1)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for d, tf in zip(self.docs, self.tf, strict=True):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out


class Retriever:
    def __init__(self) -> None:
        self.chunks = load_chunks()
        texts = [f"{c.title} {c.title} {c.text}" for c in self.chunks]   # title counts twice
        self.bm25 = BM25([_tokens(t) for t in texts])
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.mat = self.vec.fit_transform(texts)

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        q = translate_query(query)
        bm = self.bm25.scores(_tokens(q))
        tf = cosine_similarity(self.vec.transform([q]), self.mat)[0]
        fused = [0.0] * len(self.chunks)
        for scores in (bm, list(tf)):
            ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
            for rank, i in enumerate(ranked):
                if scores[i] > 0:
                    fused[i] += 1.0 / (RRF_K + rank + 1)
        order = sorted(range(len(fused)), key=lambda i: -fused[i])[:k]
        return [(self.chunks[i], round(fused[i], 5)) for i in order if fused[i] > 0]

    def get(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)


@lru_cache(maxsize=1)
def retriever() -> Retriever:
    return Retriever()
