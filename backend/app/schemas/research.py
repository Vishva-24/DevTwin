"""
app/schemas/research.py

Pydantic v2 schemas for Phase 2: Research Intelligence Engine.

These API contracts describe deterministic PDF ingestion artifacts only:
paper metadata, future chunk records, upload responses, and a lightweight
research manifest shape. They intentionally avoid chat messages and open-ended
agent memory.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaperMetadata(BaseModel):
    """Metadata extracted and persisted for a parsed paper."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        ...,
        description="Unique identifier of the stored paper record.",
    )
    title: str | None = Field(
        None,
        description="Best-effort title extracted from the PDF without AI generation.",
    )
    file_name: str = Field(
        ...,
        description="Original PDF file name supplied during upload.",
    )
    file_path: str = Field(
        ...,
        description="Server-side path where the uploaded PDF was stored for parsing.",
    )
    authors: list[str] = Field(
        default_factory=list,
        description="Author names extracted from the paper, if available.",
    )
    abstract: str | None = Field(
        None,
        description="Best-effort abstract extracted from the paper, if available.",
    )
    total_pages: int = Field(
        ...,
        ge=0,
        description="Total page count reported by the PDF parser.",
    )
    uploaded_at: datetime = Field(
        ...,
        description="UTC timestamp when the paper was uploaded and recorded.",
    )


class PaperChunkSchema(BaseModel):
    """Serialized representation of a persisted paper chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        ...,
        description="Unique identifier of the paper chunk record.",
    )
    paper_id: uuid.UUID = Field(
        ...,
        description="Identifier of the paper this chunk belongs to.",
    )
    chunk_index: int = Field(
        ...,
        ge=0,
        description="Zero-based position of this chunk within the paper.",
    )
    content: str = Field(
        ...,
        description="Markdown or plain text content stored for this chunk.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when this chunk was created.",
    )


class PaperUploadResponse(BaseModel):
    """Response returned after a PDF has been accepted, parsed, and persisted."""

    model_config = ConfigDict(from_attributes=True)

    paper_id: uuid.UUID = Field(
        ...,
        description="Unique identifier of the stored paper record.",
    )
    status: str = Field(
        default="parsed",
        description="Upload processing status. Expected value is 'parsed' after successful ingestion.",
    )
    message: str = Field(
        ...,
        description="Human-readable summary of the upload and parsing result.",
    )
    metadata: PaperMetadata = Field(
        ...,
        description="Deterministic metadata extracted from the uploaded PDF.",
    )


class ResearchManifest(BaseModel):
    """
    Strict Knowledge Object generated from a parsed research paper.

    This schema is designed for future Knowledge Graph and Research Agent
    integration. It is intentionally structured and bounded: no chat transcript,
    no open-ended answer generation, and no arbitrary extra fields.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    manifest_version: str = Field(
        default="research-manifest/v1",
        description="Version of the structured research manifest contract.",
    )
    paper_id: uuid.UUID = Field(
        ...,
        description="Identifier of the paper this knowledge object describes.",
    )
    title: str | None = Field(
        None,
        description="Paper title used when generating the manifest.",
    )
    core_concepts: list[str] = Field(
        ...,
        min_length=1,
        max_length=12,
        description="Primary research concepts suitable for future Knowledge Graph nodes.",
    )
    methodology_summary: str = Field(
        ...,
        min_length=1,
        description="Short explanation of how the research was conducted.",
    )
    key_findings: list[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="Three to five main conclusions or claims extracted from the paper.",
    )
    tech_stack_mentions: list[str] = Field(
        default_factory=list,
        max_length=15,
        description="Frameworks, libraries, tools, datasets, or platforms mentioned in the paper.",
    )
    generated_from_chunk_indices: list[int] = Field(
        default_factory=list,
        description="Paper chunk indices used as source context for manifest generation.",
    )
    source_chunk_count: int = Field(
        ...,
        ge=0,
        description="Number of chunks supplied to the manifest generator.",
    )
    limitations: str | None = Field(
        None,
        description="Brief caveat when the supplied chunk context is incomplete or weak.",
    )


class ResearchChatRequest(BaseModel):
    """Request body for grounded research chat over one paper."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User question to answer using only retrieved paper chunks.",
        examples=["What is the main architecture proposed in this paper?"],
    )


class ResearchChatResponse(BaseModel):
    """Grounded answer with exact paper chunk citations."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        ...,
        min_length=1,
        description=(
            "Answer grounded only in retrieved chunks, or the exact fallback "
            "'I cannot find this in the paper'."
        ),
    )
    citations: list[int] = Field(
        default_factory=list,
        description="Exact chunk_index values used to formulate the answer.",
    )
