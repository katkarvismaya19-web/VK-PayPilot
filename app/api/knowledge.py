from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.rag.knowledge_base import get_kb

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class NewDoc(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    markdown: str = Field(..., min_length=20)


@router.get("/stats")
def stats():
    return get_kb().stats()


@router.get("/search")
def search(q: str, k: int = 4):
    ev = get_kb().evidence(q, k)
    return {"query": q, "evidence_sufficient": ev.sufficient, "results": ev.citations()}


@router.post("/documents")
def add_document(doc: NewDoc):
    """Add a playbook/policy (markdown). It is chunked, embedded and used by the agent immediately."""
    chunks = get_kb().add_document(doc.name, doc.markdown)
    return {"added": doc.name, "chunks": chunks, **get_kb().stats()}
