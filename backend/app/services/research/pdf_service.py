"""
app/services/research/pdf_service.py

Deterministic PDF parsing and persistence service for Phase 2.

This service converts an uploaded PDF to markdown using pymupdf4llm, extracts
basic metadata with PyMuPDF, stores a Paper row, and returns a lightweight
response schema. It intentionally does not chunk text, generate embeddings,
call an LLM, or create any vector/indexing artifacts.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz
import pymupdf4llm
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research import Paper
from app.schemas.research import PaperMetadata, PaperUploadResponse

logger = logging.getLogger(__name__)

INVALID_METADATA_TITLES = {"", "untitled", "unknown", "none", "null"}
MAX_ABSTRACT_CHARS = 4000

INLINE_ABSTRACT_PATTERN = re.compile(
    r"(?is)\babstract\s*[:.-]\s*(?P<body>.*?)(?=\b(?:keywords?|introduction|background|methods?)\b|\Z)"
)
HEADING_PATTERN = re.compile(r"(?m)^\s*#\s+(.+?)\s*$")
WHITESPACE_PATTERN = re.compile(r"\s+")
AUTHOR_SPLIT_PATTERN = re.compile(r"\s*(?:;|\n|\band\b)\s*", re.IGNORECASE)
ABSTRACT_STOP_HEADINGS = {
    "keyword",
    "keywords",
    "introduction",
    "1 introduction",
    "1. introduction",
    "background",
    "method",
    "methods",
}


class PDFServiceError(Exception):
    """Base class for PDF ingestion service failures."""


class PDFParsingError(PDFServiceError):
    """Raised when the uploaded PDF cannot be parsed into deterministic text."""


class PDFStorageError(PDFServiceError):
    """Raised when parsed PDF data cannot be persisted."""


@dataclass(frozen=True)
class ExtractedPDFMetadata:
    """Basic metadata extracted deterministically from the PDF."""

    title: str
    authors: list[str]
    abstract: str | None
    total_pages: int


async def parse_and_store_pdf(
    *,
    file_path: str | Path,
    original_file_name: str,
    db: AsyncSession,
) -> PaperUploadResponse:
    """
    Parse a local PDF file, persist a Paper row, and return upload metadata.

    Args:
        file_path: Local path to the temporary PDF file.
        original_file_name: Original filename from the upload request.
        db: Request-scoped SQLAlchemy async session.

    Raises:
        PDFParsingError: If markdown or PDF metadata extraction fails.
        PDFStorageError: If the parsed paper cannot be committed to PostgreSQL.
    """
    pdf_path = Path(file_path)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise PDFParsingError(f"PDF file does not exist: {pdf_path}")

    logger.info("Starting deterministic PDF parse: %s", original_file_name)

    markdown_text = await asyncio.to_thread(_extract_markdown, pdf_path)
    metadata = await asyncio.to_thread(
        _extract_pdf_metadata,
        pdf_path,
        markdown_text,
        Path(original_file_name).stem,
    )

    paper = Paper(
        title=metadata.title,
        file_name=original_file_name,
        file_path=str(pdf_path),
        authors=metadata.authors,
        abstract=metadata.abstract,
        total_pages=metadata.total_pages,
        raw_markdown=markdown_text,
    )

    try:
        db.add(paper)
        await db.commit()
        await db.refresh(paper)
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Failed to persist parsed PDF %s: %s", original_file_name, exc)
        raise PDFStorageError("Failed to persist parsed PDF metadata.") from exc

    logger.info(
        "Stored parsed PDF: paper_id=%s title=%r pages=%d markdown_chars=%d",
        paper.id,
        paper.title,
        paper.total_pages,
        len(markdown_text),
    )

    return PaperUploadResponse(
        paper_id=paper.id,
        status="parsed",
        message=f"PDF '{original_file_name}' parsed and stored successfully.",
        metadata=PaperMetadata.model_validate(paper),
    )


def _extract_markdown(pdf_path: Path) -> str:
    """Convert a PDF to markdown with pymupdf4llm."""
    try:
        markdown = pymupdf4llm.to_markdown(str(pdf_path))
    except Exception as exc:
        logger.exception("pymupdf4llm failed for %s: %s", pdf_path, exc)
        raise PDFParsingError("Unable to extract markdown from the PDF.") from exc

    if not isinstance(markdown, str):
        raise PDFParsingError("PDF parser returned an unexpected markdown payload.")

    markdown = markdown.strip()
    if not markdown:
        raise PDFParsingError("PDF did not contain extractable markdown text.")

    return markdown


def _extract_pdf_metadata(
    pdf_path: Path,
    markdown_text: str,
    fallback_title: str,
) -> ExtractedPDFMetadata:
    """Read deterministic PDF metadata and infer missing fields from markdown."""
    document = None
    try:
        document = fitz.open(str(pdf_path))
        raw_metadata = document.metadata or {}
        total_pages = document.page_count
    except Exception as exc:
        logger.exception("PyMuPDF metadata extraction failed for %s: %s", pdf_path, exc)
        raise PDFParsingError("Unable to inspect PDF metadata.") from exc
    finally:
        if document is not None:
            document.close()

    title = _extract_title(
        metadata_title=raw_metadata.get("title"),
        markdown_text=markdown_text,
        fallback_title=fallback_title,
    )
    authors = _extract_authors(raw_metadata.get("author"))
    abstract = _extract_abstract(markdown_text)

    return ExtractedPDFMetadata(
        title=title,
        authors=authors,
        abstract=abstract,
        total_pages=total_pages,
    )


def _extract_title(
    *,
    metadata_title: str | None,
    markdown_text: str,
    fallback_title: str,
) -> str:
    """Choose a clean title from PDF metadata, markdown headings, or filename."""
    cleaned_metadata_title = _clean_metadata_value(metadata_title)
    if cleaned_metadata_title and cleaned_metadata_title.lower() not in INVALID_METADATA_TITLES:
        return cleaned_metadata_title[:1024]

    heading_match = HEADING_PATTERN.search(markdown_text)
    if heading_match:
        heading = _normalize_inline_text(heading_match.group(1))
        if heading:
            return heading[:1024]

    for line in markdown_text.splitlines():
        line = _normalize_inline_text(line.strip("# ").strip())
        if line and line.lower() not in {"abstract", "keywords", "introduction"}:
            return line[:1024]

    return fallback_title[:1024] or "Untitled PDF"


def _extract_authors(raw_author: str | None) -> list[str]:
    """Extract author names from deterministic PDF metadata."""
    author_text = _clean_metadata_value(raw_author)
    if not author_text:
        return []

    parts = [part.strip(" ,") for part in AUTHOR_SPLIT_PATTERN.split(author_text)]
    if len(parts) == 1 and "," in author_text:
        comma_parts = [part.strip() for part in author_text.split(",")]
        if 1 < len(comma_parts) <= 12:
            parts = comma_parts

    authors: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = _normalize_inline_text(part)
        key = normalized.casefold()
        if normalized and key not in seen:
            authors.append(normalized[:255])
            seen.add(key)

    return authors


def _extract_abstract(markdown_text: str) -> str | None:
    """Best-effort deterministic extraction of the Abstract section."""
    lines = markdown_text.splitlines()
    for index, line in enumerate(lines):
        if _section_key(line) != "abstract":
            continue

        collected: list[str] = []
        for next_line in lines[index + 1 :]:
            stripped = next_line.strip()
            if not stripped:
                continue

            section_key = _section_key(stripped)
            if stripped.startswith("#") or section_key in ABSTRACT_STOP_HEADINGS:
                break

            collected.append(stripped)

        abstract = _normalize_inline_text(" ".join(collected))
        return abstract[:MAX_ABSTRACT_CHARS] if abstract else None

    inline_match = INLINE_ABSTRACT_PATTERN.search(markdown_text)
    if not inline_match:
        return None

    abstract = _normalize_inline_text(inline_match.group("body"))
    return abstract[:MAX_ABSTRACT_CHARS] if abstract else None


def _clean_metadata_value(value: str | None) -> str | None:
    """Normalize optional PDF metadata strings."""
    if value is None:
        return None
    cleaned = _normalize_inline_text(value)
    return cleaned or None


def _normalize_inline_text(value: str) -> str:
    """Collapse whitespace and strip markdown/table artifacts from inline text."""
    cleaned = value.replace("|", " ").replace("*", "").replace("_", "")
    return WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def _section_key(value: str) -> str:
    """Normalize a markdown heading or section label for deterministic matching."""
    cleaned = value.strip().strip("#").strip()
    cleaned = cleaned.rstrip(":").strip()
    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned)
    return cleaned.casefold()
