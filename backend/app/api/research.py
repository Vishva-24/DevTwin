"""
app/api/research.py

FastAPI router for Phase 2 Research Intelligence ingestion endpoints.

The upload flow intentionally stops at deterministic PDF parsing and database
persistence. A separate endpoint performs deterministic chunking. This router
does not create embeddings, chat sessions, vector indexes, graph nodes, or
AI-generated research summaries.
"""

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.research import Paper
from app.schemas.research import (
    PaperMetadata,
    PaperUploadResponse,
    ResearchChatRequest,
    ResearchChatResponse,
    ResearchManifest,
)
from app.services.research.chunk_service import (
    PaperChunkStorageError,
    PaperContentMissingError,
    PaperNotFoundError,
    process_paper_chunks,
)
from app.services.research.embedding_service import (
    EmbeddingGenerationError,
    EmbeddingPaperNotFoundError,
    PaperChunksMissingError,
    VectorStorageError,
    embed_paper_chunks,
)
from app.services.research.pdf_service import PDFParsingError, PDFStorageError, parse_and_store_pdf
from app.services.research.rag_service import (
    PaperManifestNotFoundError,
    PaperManifestSourceError,
    ResearchChatServiceError,
    ResearchManifestStorageError,
    answer_paper_question,
    generate_paper_manifest,
)
from app.services.research.retrieval_service import (
    RetrievalServiceError,
    retrieve_relevant_chunks,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/research", tags=["Research"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
PDF_MAGIC_BYTES = b"%PDF-"
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
}


class ResearchQueryRequest(BaseModel):
    """Request body for retrieval-only semantic chunk search."""

    query: str = Field(
        ...,
        min_length=1,
        description="Natural-language query used to retrieve matching paper chunks.",
        examples=["What is the main architecture proposed in this paper?"],
    )


def _validate_pdf_upload_metadata(upload: UploadFile) -> None:
    """Validate filename and content type before accepting the upload stream."""
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF uploads are supported.",
        )

    if upload.content_type and upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {upload.content_type}. Expected application/pdf.",
        )


async def _save_upload_to_tempfile(upload: UploadFile, destination: Path) -> int:
    """
    Stream an UploadFile into a temporary PDF path with size and magic-byte checks.

    The caller owns the surrounding TemporaryDirectory, so the saved file is
    removed automatically after parsing succeeds or fails.
    """
    first_bytes = await upload.read(len(PDF_MAGIC_BYTES))
    if first_bytes != PDF_MAGIC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded file is not a valid PDF.",
        )

    bytes_written = len(first_bytes)

    with destination.open("wb") as temp_pdf:
        await asyncio.to_thread(temp_pdf.write, first_bytes)

        while chunk := await upload.read(READ_CHUNK_BYTES):
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"PDF upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )
            await asyncio.to_thread(temp_pdf.write, chunk)

    return bytes_written


@router.get(
    "/papers",
    response_model=list[PaperMetadata],
    status_code=status.HTTP_200_OK,
    summary="List uploaded research papers",
)
async def list_research_papers(
    db: AsyncSession = Depends(get_db),
) -> list[PaperMetadata]:
    """Return uploaded paper metadata without raw markdown or chunk content."""
    result = await db.execute(select(Paper).order_by(Paper.uploaded_at.desc()))
    papers = result.scalars().all()
    return [PaperMetadata.model_validate(paper) for paper in papers]


@router.post(
    "/upload",
    response_model=PaperUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse a research PDF",
)
async def upload_research_pdf(
    file: UploadFile = File(..., description="Academic paper or research PDF to parse."),
    db: AsyncSession = Depends(get_db),
) -> PaperUploadResponse:
    """
    Accept a PDF, parse it to markdown, persist Paper metadata, and delete temp files.

    The PDF is streamed into a secure TemporaryDirectory and removed before the
    response is returned. The database stores extracted markdown, not the PDF.
    """
    _validate_pdf_upload_metadata(file)

    try:
        with tempfile.TemporaryDirectory(prefix="devtwin_research_pdf_") as temp_dir:
            temp_pdf_path = Path(temp_dir) / f"{uuid.uuid4().hex}.pdf"
            upload_size = await _save_upload_to_tempfile(file, temp_pdf_path)
            logger.info(
                "Saved temporary PDF upload: filename=%s bytes=%d temp_path=%s",
                file.filename,
                upload_size,
                temp_pdf_path,
            )

            return await parse_and_store_pdf(
                file_path=temp_pdf_path,
                original_file_name=file.filename or "uploaded.pdf",
                db=db,
            )

    except HTTPException:
        raise
    except PDFParsingError as exc:
        logger.warning("PDF parsing failed for %s: %s", file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PDFStorageError as exc:
        logger.exception("PDF persistence failed for %s: %s", file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF was parsed but could not be saved.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected PDF upload failure for %s: %s", file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload and parse PDF.",
        ) from exc
    finally:
        await file.close()


@router.post(
    "/{paper_id}/chunk",
    status_code=status.HTTP_200_OK,
    summary="Chunk a parsed research paper",
)
async def chunk_research_paper(
    paper_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """
    Generate deterministic sliding-window chunks for an already parsed paper.

    This endpoint reads the stored raw_markdown from the papers table and
    replaces paper_chunks for the paper. It does not create embeddings or
    perform AI summarization.
    """
    try:
        result = await process_paper_chunks(paper_id=paper_id, db=db)
    except PaperNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PaperContentMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PaperChunkStorageError as exc:
        logger.exception("Chunk persistence failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Paper chunks could not be persisted.",
        ) from exc

    return {
        "status": "chunked",
        "paper_id": str(result.paper_id),
        "total_chunks": result.total_chunks,
    }


@router.post(
    "/{paper_id}/embed",
    status_code=status.HTTP_200_OK,
    summary="Embed paper chunks into local Qdrant",
)
async def embed_research_paper(
    paper_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    """
    Generate local embeddings for stored paper chunks and save them to Qdrant.

    This endpoint only performs embedding generation and vector storage. It does
    not perform semantic search, retrieval, RAG, or answer generation.
    """
    try:
        result = await embed_paper_chunks(paper_id=paper_id, db=db)
    except EmbeddingPaperNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PaperChunksMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except EmbeddingGenerationError as exc:
        logger.exception("Embedding generation failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Paper chunks could not be embedded.",
        ) from exc
    except VectorStorageError as exc:
        logger.exception("Vector storage failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Paper chunk embeddings could not be stored.",
        ) from exc

    return {
        "status": "embedded",
        "paper_id": str(result.paper_id),
        "total_vectors_stored": result.total_vectors_stored,
        "collection_name": result.collection_name,
        "embedding_model": result.embedding_model,
    }


@router.post(
    "/{paper_id}/query",
    status_code=status.HTTP_200_OK,
    summary="Retrieve relevant paper chunks",
)
async def query_research_paper(
    paper_id: uuid.UUID,
    request: ResearchQueryRequest,
) -> list[dict[str, str | int | float | None]]:
    """
    Retrieve top matching raw chunks for a query scoped to one paper.

    This is retrieval only. It embeds the query, searches Qdrant, and returns
    raw chunk text plus similarity scores without LLM generation.
    """
    try:
        return await retrieve_relevant_chunks(
            query=request.query,
            paper_id=paper_id,
            limit=5,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except RetrievalServiceError as exc:
        logger.exception("Retrieval failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Relevant chunks could not be retrieved.",
        ) from exc


@router.post(
    "/{paper_id}/manifest",
    response_model=ResearchManifest,
    status_code=status.HTTP_200_OK,
    summary="Generate a structured research manifest",
)
async def generate_research_manifest(
    paper_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ResearchManifest:
    """
    Generate and persist a strict ResearchManifest Knowledge Object.

    This endpoint uses selected paper chunks as bounded context for Gemini 2.5
    Flash and returns validated structured JSON. It does not create a chat
    interface or open-ended RAG answer.
    """
    try:
        return await generate_paper_manifest(paper_id=paper_id, db=db)
    except PaperManifestNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PaperManifestSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ResearchManifestStorageError as exc:
        logger.exception("Manifest persistence failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Research manifest could not be saved.",
        ) from exc


@router.post(
    "/{paper_id}/chat",
    response_model=ResearchChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask a grounded question about a research paper",
)
async def chat_with_research_paper(
    paper_id: uuid.UUID,
    request: ResearchChatRequest,
) -> ResearchChatResponse:
    """
    Answer a question using only retrieved chunks from one paper.

    The answer is generated by Gemini 2.5 Flash with a strict grounding prompt
    and must include exact chunk_index citations.
    """
    try:
        response = await answer_paper_question(
            paper_id=paper_id,
            query=request.query,
        )
        return ResearchChatResponse.model_validate(response)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ResearchChatServiceError as exc:
        logger.exception("Grounded chat failed for paper_id=%s: %s", paper_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Grounded research chat could not be completed.",
        ) from exc
