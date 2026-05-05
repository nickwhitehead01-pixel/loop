"""
Document ingestion and ChromaDB similarity search.

ingest_lesson  — parse a document, chunk with 500/50 token splits, embed, store LessonChunks
search         — embed a query, return top-k chunk texts
"""
from __future__ import annotations

from io import BytesIO

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import LessonChunk
from app.services.chroma_client import lesson_chunks_col
from app.services.ollama_client import embed, embed_batch

# Chunks smaller than this (chars) are filtered after splitting — avoids noise
MIN_CHUNK_CHARS = 80

# 500-token chunks with 50-token overlap using the GPT-2 tokenizer
# (closest public approximation to nomic-embed-text's vocabulary size)
_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    encoding_name="gpt2",
    chunk_size=500,
    chunk_overlap=50,
)

ACCEPTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}


# ---------------------------------------------------------------------------
# Text sanitization — remove invalid UTF-8 characters
# ---------------------------------------------------------------------------

def _sanitize_text(text: str) -> str:
    """Remove null bytes and other problematic characters that break UTF-8 encoding."""
    return "".join(char if ord(char) >= 32 or char in "\n\r\t" else " " for char in text)


# ---------------------------------------------------------------------------
# Text extraction — one function per format
# ---------------------------------------------------------------------------

def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text = _sanitize_text(text)
            pages.append(text)
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document  # python-docx
    doc = Document(BytesIO(data))
    paragraphs = [_sanitize_text(p.text) for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation  # python-pptx
    prs = Presentation(BytesIO(data))
    slides = []
    for slide in prs.slides:
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = _sanitize_text(para.text.strip())
                    if line:
                        texts.append(line)
        if texts:
            slides.append("\n".join(texts))
    return "\n\n".join(slides)


def _extract_txt(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return _sanitize_text(text)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """
    Dispatch to the correct parser based on file extension.
    Raises ValueError for unsupported types.
    """
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_bytes)
    elif ext == ".docx":
        return _extract_docx(file_bytes)
    elif ext == ".pptx":
        return _extract_pptx(file_bytes)
    elif ext == ".txt":
        return _extract_txt(file_bytes)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Accepted: {', '.join(sorted(ACCEPTED_EXTENSIONS))}"
        )


def _chunk_text(text: str) -> list[str]:
    """
    Split *text* into 500-token chunks with 50-token overlap.
    Uses RecursiveCharacterTextSplitter with the GPT-2 tiktoken encoder.
    Chunks shorter than MIN_CHUNK_CHARS chars are discarded (page numbers, etc.).
    """
    raw_chunks = _splitter.split_text(text)
    return [c for c in raw_chunks if len(c.strip()) >= MIN_CHUNK_CHARS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ingest_lesson(
    lesson_id: int,
    file_bytes: bytes,
    db: AsyncSession,
    filename: str = "upload.pdf",
) -> int:
    """
    Parse *file_bytes* (PDF, DOCX, PPTX, or TXT), embed each chunk,
    and store into lesson_chunks. Returns the number of chunks stored.

    All chunk embeddings are requested in a single batched call to Ollama,
    so upload time is one HTTP round-trip regardless of document length.
    """
    text = extract_text(file_bytes, filename)
    chunks = _chunk_text(text)

    if not chunks:
        return 0

    # One batched embed call instead of N sequential calls
    vectors = await embed_batch(chunks)

    # Persist content in SQLite (no embedding column) and embeddings in ChromaDB
    col = lesson_chunks_col()
    chroma_ids: list[str] = []
    chroma_embeddings: list[list[float]] = []
    chroma_documents: list[str] = []
    chroma_metadatas: list[dict] = []

    for i, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
        chunk = LessonChunk(
            lesson_id=lesson_id,
            chunk_index=i,
            content=chunk_text,
        )
        db.add(chunk)

    await db.flush()

    # Re-query IDs now that flush assigned them
    result = await db.execute(
        select(LessonChunk.id, LessonChunk.content, LessonChunk.chunk_index)
        .where(LessonChunk.lesson_id == lesson_id)
        .order_by(LessonChunk.chunk_index)
    )
    rows = result.all()

    for (chunk_id, chunk_content, chunk_index), vector in zip(rows, vectors):
        chroma_ids.append(str(chunk_id))
        chroma_embeddings.append(vector)
        chroma_documents.append(chunk_content)
        chroma_metadatas.append({"lesson_id": str(lesson_id), "chunk_index": chunk_index})

    col.add(
        ids=chroma_ids,
        embeddings=chroma_embeddings,
        documents=chroma_documents,
        metadatas=chroma_metadatas,
    )

    return len(rows)


async def search(
    query: str,
    db: AsyncSession,
    k: int = 5,
    lesson_id: int | None = None,
) -> list[str]:
    """
    Embed *query* and return the top-k most similar lesson chunk texts.
    Optionally filter by lesson_id.
    """
    vector = await embed(query)
    col = lesson_chunks_col()

    kwargs: dict = {"query_embeddings": [vector], "n_results": k, "include": ["documents"]}
    if lesson_id is not None:
        kwargs["where"] = {"lesson_id": str(lesson_id)}

    results = col.query(**kwargs)
    docs = results.get("documents") or [[]]
    return list(docs[0])


def delete_lesson_chunks(lesson_id: int) -> None:
    """Remove all ChromaDB entries for *lesson_id*. Call before deleting SQLite rows."""
    col = lesson_chunks_col()
    try:
        col.delete(where={"lesson_id": str(lesson_id)})
    except Exception:
        pass
