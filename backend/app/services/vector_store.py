"""
PDF ingestion and pgvector similarity search.

ingest_lesson  — parse a PDF, chunk by paragraph, embed, store LessonChunks
search         — embed a query, return top-k chunk texts
"""
from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import LessonChunk
from app.services.ollama_client import embed

# Chunks smaller than this (chars) are skipped — avoids noise from page numbers etc.
MIN_CHUNK_CHARS = 80
# Target chunk size in characters (soft limit — splits on paragraph boundaries)
TARGET_CHUNK_CHARS = 800

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
    Split *text* into chunks no larger than TARGET_CHUNK_CHARS.
    Splits preferably at double-newlines (paragraph boundaries) then
    at sentence endings if a paragraph is too long.
    """
    # Normalise whitespace
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(buffer) + len(para) <= TARGET_CHUNK_CHARS:
            buffer = (buffer + "\n\n" + para).strip()
        else:
            if buffer:
                chunks.append(buffer)
            # Para itself may exceed target — split at sentence boundaries
            if len(para) > TARGET_CHUNK_CHARS:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sub_buf = ""
                for sent in sentences:
                    if len(sub_buf) + len(sent) <= TARGET_CHUNK_CHARS:
                        sub_buf = (sub_buf + " " + sent).strip()
                    else:
                        if sub_buf:
                            chunks.append(sub_buf)
                        sub_buf = sent
                if sub_buf:
                    buffer = sub_buf
                else:
                    buffer = ""
            else:
                buffer = para

    if buffer:
        chunks.append(buffer)

    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


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
    """
    text = extract_text(file_bytes, filename)
    chunks = _chunk_text(text)

    for chunk_text in chunks:
        vector = await embed(chunk_text)
        db.add(LessonChunk(
            lesson_id=lesson_id,
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
