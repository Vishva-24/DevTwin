"""
app/services/research/embedding_service.py

Local embedding generation for Research Brain chunks.

This service uses FastEmbed with BAAI/bge-small-en-v1.5 to generate embeddings
for already-persisted PaperChunk rows, then stores those vectors in local
Qdrant file mode. It does not implement retrieval, semantic search, RAG, or AI
summarization.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import Paper, PaperChunk
from app.services.research.qdrant_service import (
    COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    QdrantServiceError,
    delete_paper_vectors,
    upsert_paper_chunks,
)

logger = logging.getLogger(__name__)

EMBEDDING_CACHE_DIR = Path(".fastembed_cache")
EMBEDDING_BATCH_SIZE = 64


class EmbeddingServiceError(Exception):
    """Base class for embedding pipeline failures."""


class EmbeddingPaperNotFoundError(EmbeddingServiceError):
    """Raised when a requested paper does not exist."""


class PaperChunksMissingError(EmbeddingServiceError):
    """Raised when no chunks exist for a paper."""


class EmbeddingGenerationError(EmbeddingServiceError):
    """Raised when the local embedding model cannot generate vectors."""


class VectorStorageError(EmbeddingServiceError):
    """Raised when vectors cannot be stored in Qdrant."""


@dataclass(frozen=True)
class EmbeddingProcessingResult:
    """Result returned after paper chunks have been embedded and stored."""

    paper_id: uuid.UUID
    total_vectors_stored: int
    collection_name: str
    embedding_model: str


@lru_cache(maxsize=1)
def get_embedding_model() -> TextEmbedding:
    """Load the local FastEmbed model once per process."""
    EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=EMBEDDING_MODEL_NAME,
        cache_dir=str(EMBEDDING_CACHE_DIR),
    )


async def embed_paper_chunks(
    paper_id: uuid.UUID,
    db: AsyncSession,
) -> EmbeddingProcessingResult:
    """
    Generate embeddings for all chunks belonging to a paper and store them in Qdrant.

    The endpoint is intentionally idempotent for a paper: existing vectors for
    that paper are deleted before fresh vectors are inserted.
    """
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise EmbeddingPaperNotFoundError(f"Paper {paper_id} was not found.")

    stmt = (
        select(PaperChunk)
        .where(PaperChunk.paper_id == paper_id)
        .order_by(PaperChunk.chunk_index.asc())
    )
    chunks = list((await db.execute(stmt)).scalars().all())
    if not chunks:
        raise PaperChunksMissingError(
            f"Paper {paper_id} has no chunks. Run POST /api/v1/research/{paper_id}/chunk first."
        )

    texts = [chunk.content for chunk in chunks]
    vectors = await asyncio.to_thread(_embed_texts_sync, texts)

    payloads = [
        {
            "paper_id": str(chunk.paper_id),
            "chunk_id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "text_content": chunk.content,
        }
        for chunk in chunks
    ]

    try:
        total_vectors_stored = await asyncio.to_thread(
            _replace_paper_vectors_sync,
            paper_id,
            vectors,
            payloads,
        )
    except QdrantServiceError as exc:
        raise VectorStorageError(f"Failed to store vectors for paper {paper_id}.") from exc

    logger.info(
        "Embedded paper_id=%s chunks=%d vectors=%d model=%s collection=%s.",
        paper_id,
        len(chunks),
        total_vectors_stored,
        EMBEDDING_MODEL_NAME,
        COLLECTION_NAME,
    )

    return EmbeddingProcessingResult(
        paper_id=paper_id,
        total_vectors_stored=total_vectors_stored,
        collection_name=COLLECTION_NAME,
        embedding_model=EMBEDDING_MODEL_NAME,
    )


async def embed_query(query: str) -> list[float]:
    """
    Embed one user query with the same local FastEmbed model used for chunks.

    This produces a 384-dimensional vector suitable for cosine search in the
    research_papers Qdrant collection. It does not call an LLM.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Query must not be empty.")

    return await asyncio.to_thread(_embed_query_sync, cleaned_query)


def _embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """Generate local embeddings in a blocking worker thread."""
    try:
        model = get_embedding_model()
        embeddings = model.embed(texts, batch_size=EMBEDDING_BATCH_SIZE)
        return [embedding.tolist() for embedding in embeddings]
    except Exception as exc:
        logger.exception("Local embedding generation failed: %s", exc)
        raise EmbeddingGenerationError("Failed to generate local embeddings.") from exc


def _replace_paper_vectors_sync(
    paper_id: uuid.UUID,
    vectors: list[list[float]],
    payloads: list[dict[str, object]],
) -> int:
    """Replace all vectors for one paper in local Qdrant."""
    delete_paper_vectors(paper_id)
    return upsert_paper_chunks(vectors=vectors, payloads=payloads)


def _embed_query_sync(query: str) -> list[float]:
    """Generate a single query embedding in a blocking worker thread."""
    try:
        model = get_embedding_model()
        embedding = next(iter(model.query_embed(query, batch_size=1)))
        return [float(value) for value in embedding.tolist()]
    except StopIteration as exc:
        raise EmbeddingGenerationError("Embedding model returned no query vector.") from exc
    except Exception as exc:
        logger.exception("Local query embedding generation failed: %s", exc)
        raise EmbeddingGenerationError("Failed to generate local query embedding.") from exc
