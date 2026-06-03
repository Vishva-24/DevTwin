"""
app/services/research/retrieval_service.py

Retrieval-only semantic search for Phase 2 Research Brain.

This module embeds a user query locally and retrieves matching chunks from
Qdrant. It intentionally does not call Gemini/OpenAI, generate answers, build a
chat loop, or perform RAG synthesis.
"""

import logging
import uuid
from typing import Any

from app.services.research.embedding_service import EmbeddingGenerationError, embed_query
from app.services.research.qdrant_service import QdrantServiceError, search_chunks

logger = logging.getLogger(__name__)


class RetrievalServiceError(Exception):
    """Raised when retrieval-only semantic search fails."""


async def retrieve_relevant_chunks(
    query: str,
    paper_id: uuid.UUID,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Embed a query and retrieve the top matching chunks for a paper.

    Args:
        query: Natural-language search query.
        paper_id: Paper UUID used to scope Qdrant results.
        limit: Maximum number of results to return.

    Returns:
        Ranked raw chunks with similarity scores and chunk metadata.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Query must not be empty.")
    if limit <= 0:
        raise ValueError("limit must be greater than 0.")

    try:
        query_vector = await embed_query(cleaned_query)
        raw_results = await search_chunks(
            query_vector=query_vector,
            paper_id=paper_id,
            limit=limit,
        )
    except (EmbeddingGenerationError, QdrantServiceError) as exc:
        logger.exception("Retrieval failed for paper_id=%s: %s", paper_id, exc)
        raise RetrievalServiceError(f"Failed to retrieve chunks for paper {paper_id}.") from exc

    return [_format_retrieval_result(result) for result in raw_results]


def _format_retrieval_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Qdrant hit into a stable API response shape."""
    return {
        "paper_id": result.get("paper_id"),
        "chunk_id": result.get("chunk_id"),
        "chunk_index": result.get("chunk_index"),
        "score": result.get("score"),
        "text_content": result.get("text_content", ""),
    }
