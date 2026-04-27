"""
PDF ingestion and pgvector similarity search.

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

    for i, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
        db.add(LessonChunk(
            lesson_id=lesson_id,
            chunk_index=i,
            content=chunk_text,
            embedding=vector,
        ))

    await db.flush()
    return len(chunks)


async def search(
    query: str,
    db: AsyncSession,
    k: int = 5,
) -> list[str]:
    """
    Embed *query* and return the top-k most similar lesson chunk texts.
    """
    vector = await embed(query)
    result = await db.execute(
        select(LessonChunk.content)
        .order_by(LessonChunk.embedding.cosine_distance(vector))
        .limit(k)
    )
    return list(result.scalars().all())
