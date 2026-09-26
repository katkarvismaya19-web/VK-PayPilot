"""
Section-aware chunking. Mirrors VeriRAG's paragraph chunker + text_chunker:
split markdown by headings (so each chunk carries its section for citations),
then fall back to overlapping character windows for long sections.
"""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    doc: str
    title: str
    section: str
    text: str
    chunk_index: int


def chunk_text(text: str, chunk_size: int = 900, chunk_overlap: int = 150) -> list[str]:
    """Overlapping window chunker (same contract as VeriRAG rag/ingestion/chunking/text_chunker.py)."""
    if not text.strip():
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    text, chunks, start = text.strip(), [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if (piece := text[start:end].strip()):
            chunks.append(piece)
        if end == len(text):
            break
        start = end - chunk_overlap
    return chunks


def _pack_paragraphs(body: str, max_chars: int = 1200) -> list[str]:
    """Paragraph chunker (as in VeriRAG paragraph_chunker): keep whole paragraphs together
    up to max_chars; only fall back to windowed splitting for a single oversized paragraph."""
    out, cur = [], ""
    for para in [p.strip() for p in body.split("\n") if p.strip()]:
        if len(para) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(chunk_text(para, max_chars, 150))
        elif len(cur) + len(para) + 1 > max_chars:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def chunk_markdown(doc: str, markdown: str) -> list[Chunk]:
    title, section, buf, out = doc, "Overview", [], []

    def flush():
        for piece in _pack_paragraphs("\n".join(buf)):
            out.append(Chunk(doc=doc, title=title, section=section, text=piece, chunk_index=len(out)))
        buf.clear()

    for line in markdown.splitlines():
        if m := re.match(r"^(#{1,3})\s+(.*)", line):
            flush()
            if len(m.group(1)) == 1:
                title = m.group(2).strip()
            else:
                section = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return out
