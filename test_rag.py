from app.rag.chunker import chunk_markdown, chunk_text
from app.rag.knowledge_base import get_kb


def test_chunk_text_overlap():
    chunks = chunk_text("a" * 2000, chunk_size=900, chunk_overlap=150)
    assert len(chunks) == 3 and all(len(c) <= 900 for c in chunks)


def test_markdown_chunks_keep_sections():
    chunks = chunk_markdown("doc", "# Title\n## Section A\nFirst para.\n## Section B\nSecond para.")
    assert [(c.title, c.section) for c in chunks] == [("Title", "Section A"), ("Title", "Section B")]


def test_retrieves_right_policy():
    ev = get_kb().evidence("should I give a discount on a failed payment")
    assert ev.sufficient
    assert ev.items[0].title == "Payment Failure Recovery Playbook"


def test_off_topic_is_insufficient():
    assert not get_kb().evidence("what is the capital of peru").sufficient


def test_knowledge_search_endpoint(client):
    r = client.get("/api/knowledge/search", params={"q": "quiet hours at night"}).json()
    assert r["results"][0]["section"] == "Quiet hours"
