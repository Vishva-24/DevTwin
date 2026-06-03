"""
app/services/research/chunk_service.py

Deterministic sliding-window chunking for Phase 2 Research Intelligence.

This module intentionally uses native Python string processing only. It does
not introduce LangChain, LlamaIndex, embeddings, vector stores, or NLP-specific
chunking libraries.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import Paper, PaperChunk

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 200
START_ALIGNMENT_WINDOW = 80


class ChunkServiceError(Exception):
    """Base class for deterministic chunking service failures."""


class PaperNotFoundError(ChunkServiceError):
    """Raised when a requested paper does not exist."""


class PaperContentMissingError(ChunkServiceError):
    """Raised when a paper has no raw markdown to chunk."""


class PaperChunkStorageError(ChunkServiceError):
    """Raised when generated chunks cannot be persisted."""


@dataclass(frozen=True)
class ChunkProcessingResult:
    """Result returned after a paper has been chunked and persisted."""

    paper_id: uuid.UUID
    total_chunks: int


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """
    Split text into deterministic sliding-window chunks.

    The chunker prefers natural boundaries in this order:
      1. Double newline paragraph breaks.
      2. Single whitespace boundaries.
      3. Hard character limit when no clean boundary exists.

    Args:
        text: Source text to chunk.
        chunk_size: Maximum target size for each chunk in characters.
        overlap: Number of characters repeated between adjacent chunks.

    Returns:
        Ordered list of non-empty chunk strings.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0.")
    if overlap < 0:
        raise ValueError("overlap must be greater than or equal to 0.")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size.")

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_text:
        return []
    if len(normalized_text) <= chunk_size:
        return [normalized_text]

    chunks: list[str] = []
    text_length = len(normalized_text)
    start = 0
    minimum_soft_split = max(1, chunk_size // 2)

    while start < text_length:
        hard_end = min(start + chunk_size, text_length)
        if hard_end == text_length:
            final_chunk = normalized_text[start:].strip()
            if final_chunk:
                chunks.append(final_chunk)
            break

        split_end = _find_best_split_end(
            text=normalized_text,
            start=start,
            hard_end=hard_end,
            minimum_soft_split=minimum_soft_split,
        )
        if split_end <= start:
            logger.warning(
                "Chunk split did not advance; forcing hard window advance at start=%d.",
                start,
            )
            split_end = hard_end

        chunk = normalized_text[start:split_end].strip()
        if chunk:
            chunks.append(chunk)

        next_start = max(0, split_end - overlap)
        if next_start <= start:
            next_start = split_end

        start = _align_start_to_boundary(
            text=normalized_text,
            candidate_start=next_start,
            upper_bound=split_end,
        )

    return chunks


async def process_paper_chunks(
    paper_id: uuid.UUID,
    db: AsyncSession,
) -> ChunkProcessingResult:
    """
    Generate and persist deterministic chunks for a stored paper.

    Existing chunks for the paper are replaced so the endpoint is idempotent
    and can be safely re-run after changing chunking parameters in the future.
    """
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise PaperNotFoundError(f"Paper {paper_id} was not found.")

    if not paper.raw_markdown or not paper.raw_markdown.strip():
        raise PaperContentMissingError(f"Paper {paper_id} has no raw markdown to chunk.")

    markdown_length = len(paper.raw_markdown)
    logger.info(
        "Starting chunk generation for paper_id=%s title=%r markdown_chars=%d.",
        paper_id,
        paper.title,
        markdown_length,
    )

    chunks = chunk_text(paper.raw_markdown)
    if not chunks:
        raise PaperContentMissingError(f"Paper {paper_id} produced no chunkable text.")

    logger.info("Generated %d chunks for paper_id=%s.", len(chunks), paper_id)
    logger.info(
        "Chunk size stats for paper_id=%s: first=%d last=%d max=%d.",
        paper_id,
        len(chunks[0]),
        len(chunks[-1]),
        max(len(chunk) for chunk in chunks),
    )

    rows = [
        {
            "id": uuid.uuid4(),
            "paper_id": paper.id,
            "chunk_index": index,
            "content": content,
        }
        for index, content in enumerate(chunks)
    ]

    try:
        delete_result = await db.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper.id))
        deleted_count = delete_result.rowcount if delete_result.rowcount is not None else 0
        logger.info(
            "Deleted %d existing chunks for paper_id=%s before inserting fresh chunks.",
            deleted_count,
            paper_id,
        )

        await db.execute(insert(PaperChunk), rows)
        inserted_count = len(rows)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Failed to persist chunks for paper_id=%s: %s", paper_id, exc)
        raise PaperChunkStorageError(f"Failed to persist chunks for paper {paper_id}.") from exc

    logger.info(
        "Successfully inserted %d chunks for paper_id=%s with chunk_index range 0..%d.",
        inserted_count,
        paper_id,
        len(rows) - 1,
    )
    return ChunkProcessingResult(paper_id=paper.id, total_chunks=len(chunks))


def _find_best_split_end(
    *,
    text: str,
    start: int,
    hard_end: int,
    minimum_soft_split: int,
) -> int:
    """Find the best natural split boundary within the current chunk window."""
    lower_bound = min(start + minimum_soft_split, hard_end)

    paragraph_break = text.rfind("\n\n", lower_bound, hard_end)
    if paragraph_break != -1:
        return paragraph_break + 2

    whitespace_break = _rfind_whitespace(text, lower_bound, hard_end)
    if whitespace_break != -1:
        return whitespace_break + 1

    return hard_end


def _rfind_whitespace(text: str, lower_bound: int, upper_bound: int) -> int:
    """Return the rightmost whitespace index inside a bounded text slice."""
    for index in range(upper_bound - 1, lower_bound - 1, -1):
        if text[index].isspace():
            return index
    return -1


def _align_start_to_boundary(
    *,
    text: str,
    candidate_start: int,
    upper_bound: int,
) -> int:
    """
    Nudge the next chunk start forward to avoid beginning inside a word.

    The search is intentionally small so overlap remains meaningful.
    """
    if candidate_start <= 0:
        return 0
    if candidate_start >= upper_bound:
        return upper_bound
    if text[candidate_start].isspace():
        return candidate_start + 1
    if text[candidate_start - 1].isspace():
        return candidate_start

    search_end = min(upper_bound, candidate_start + START_ALIGNMENT_WINDOW)
    for index in range(candidate_start, search_end):
        if text[index].isspace():
            return index + 1

    return candidate_start
