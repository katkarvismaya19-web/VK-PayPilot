"""
Knowledge base: ingestion, retrieval and evidence building.

Pipeline (same stages as VeriRAG):
  ingest (markdown) -> chunk -> embed -> vector search -> evidence context
  -> grounded answer with validated citations / insufficient-evidence detection
"""
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.rag.chunker import Chunk, chunk_markdown
from app.rag.embeddings import make_embedder

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
MIN_EVIDENCE_SCORE = 0.12


@dataclass
class RetrievalResult:
    doc: str
    title: str
    section: str
    text: str
    score: float


@dataclass
class EvidenceItem:
    citation_id: int
    doc: str
    title: str
    section: str
    text: str
    score: float


@dataclass
class EvidenceContext:
    items: list[EvidenceItem] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return bool(self.items) and self.items[0].score >= MIN_EVIDENCE_SCORE

    def as_prompt(self) -> str:
        return "\n\n".join(f"[{e.citation_id}] ({e.title} / {e.section})\n{e.text}" for e in self.items)

    def citations(self) -> list[dict]:
        return [{"id": e.citation_id, "source": e.title, "section": e.section,
                 "score": round(e.score, 3), "text": e.text} for e in self.items]


class KnowledgeBase:
    def __init__(self, directory: Path = KNOWLEDGE_DIR):
        self.directory = directory
        self.chunks: list[Chunk] = []
        self.embedder = make_embedder()
        self.matrix: np.ndarray | None = None
        self.reload()

    def reload(self) -> None:
        self.chunks = []
        for path in sorted(self.directory.glob("*.md")):
            self.chunks.extend(chunk_markdown(path.stem, path.read_text(encoding="utf-8")))
        texts = [f"{c.title}. {c.section}. {c.text}" for c in self.chunks]
        self.embedder.fit(texts)
        self.matrix = np.array(self.embedder.embed_texts(texts)) if texts else None

    def add_document(self, name: str, markdown: str) -> int:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "doc"
        (self.directory / f"{safe}.md").write_text(markdown, encoding="utf-8")
        self.reload()
        return sum(1 for c in self.chunks if c.doc == safe)

    def search(self, query: str, k: int = 4) -> list[RetrievalResult]:
        if self.matrix is None or not query.strip():
            return []
        q = np.array(self.embedder.embed_text(query))
        scores = self.matrix @ q
        top = np.argsort(-scores)[:k]
        return [RetrievalResult(doc=self.chunks[i].doc, title=self.chunks[i].title, section=self.chunks[i].section,
                                text=self.chunks[i].text, score=float(scores[i])) for i in top if scores[i] > 0]

    def evidence(self, query: str, k: int = 4) -> EvidenceContext:
        return EvidenceContext(items=[EvidenceItem(citation_id=i, **asdict(r))
                                      for i, r in enumerate(self.search(query, k), start=1)])

    def stats(self) -> dict:
        docs = sorted({c.title for c in self.chunks})
        return {"documents": len(docs), "chunks": len(self.chunks), "titles": docs,
                "embedder": type(self.embedder).__name__}


@lru_cache
def get_kb() -> KnowledgeBase:
    return KnowledgeBase()
