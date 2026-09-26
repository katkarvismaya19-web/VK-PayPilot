"""
Embedding services.

VeriRAG uses BAAI/bge-small-en-v1.5 via sentence-transformers. That needs a
model download, so PayPilot defaults to a local TF-IDF embedder with the same
interface (embed_text / embed_texts, L2-normalised) and switches to bge
automatically when EMBEDDINGS=bge and sentence-transformers is installed.
"""
import math
import os
import re
from collections import Counter

import numpy as np

STOP = set("a an the and or of to in for on at by is are be as it this that with from not no do does if then "
           "than their they them its into only should must may can will was were has have had use used what how why "
           "when which who where whom much many i my me we our you your about any some there here so also just get "
           "got give want need tell show would could per "
           # fragments left by contractions (won't, don't ...) that would otherwise match real words like "won"
           "won don isn aren wasn weren doesn didn hasn haven hadn shouldn wouldn couldn ll ve re".split())


def tokenize(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]
    return [stem(t) for t in toks]


def stem(t: str) -> str:
    """Tiny consistent stemmer: payments/payment -> pay, discounts -> discount, abandoned -> abandon."""
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 5:
        t = re.sub(r"(ment|ing|ed)$", "", t)
    return t


class TfidfEmbedder:
    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        docs = [set(tokenize(t)) for t in texts]
        df = Counter(w for d in docs for w in d)
        self.vocab = {w: i for i, w in enumerate(sorted(df))}
        n = len(texts)
        self.idf = np.array([math.log((1 + n) / (1 + df[w])) + 1 for w in sorted(df)])
        return self

    def embed_text(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Text must not be empty.")
        vec = np.zeros(len(self.vocab))
        for w, c in Counter(tokenize(text)).items():
            if w in self.vocab:
                vec[self.vocab[w]] = (1 + math.log(c)) * self.idf[self.vocab[w]]
        norm = np.linalg.norm(vec)
        return (vec / norm if norm else vec).tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


class BgeEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)

    def fit(self, texts):
        return self

    def embed_text(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def make_embedder():
    if os.getenv("EMBEDDINGS", "tfidf").lower() == "bge":
        try:
            return BgeEmbedder()
        except Exception:
            pass
    return TfidfEmbedder()
