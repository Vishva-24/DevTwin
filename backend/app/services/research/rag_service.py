"""
app/services/research/rag_service.py

Structured Research Manifest generation for Phase 2 Research Brain.

Despite the module name, this is not an open-ended chat or answer-generation
pipeline. It selects bounded paper chunks, asks Gemini 2.5 Flash for a strict
JSON Knowledge Object, validates that JSON with Pydantic, and persists it on
the Paper row.

The same module also hosts grounded research chat. Chat responses are generated
only from retrieved chunks and must include exact chunk_index citations.
"""

import json
import logging
import re
import uuid
import warnings
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.research import Paper, PaperChunk
from app.schemas.research import ResearchChatResponse, ResearchManifest
from app.services.research.retrieval_service import RetrievalServiceError, retrieve_relevant_chunks

logger = logging.getLogger(__name__)

GEMINI_MANIFEST_MODEL = "gemini-2.5-flash"
MAX_CONTEXT_CHUNKS = 8
MAX_CONTEXT_CHARS = 28_000
MAX_CHUNK_CHARS = 4_000
CHAT_RETRIEVAL_LIMIT = 5
CHAT_SYSTEM_INSTRUCTION = (
    "You are an academic research assistant. Answer the user's query using ONLY "
    "the provided text chunks. If the answer is not in the text, say "
    "'I cannot find this in the paper'. Do not hallucinate."
)
CANNOT_FIND_ANSWER = "I cannot find this in the paper"


class ResearchManifestServiceError(Exception):
    """Base class for manifest generation failures."""


class PaperManifestNotFoundError(ResearchManifestServiceError):
    """Raised when a requested paper does not exist."""


class PaperManifestSourceError(ResearchManifestServiceError):
    """Raised when a paper has no usable text chunks for manifest generation."""


class ResearchManifestStorageError(ResearchManifestServiceError):
    """Raised when the generated manifest cannot be persisted."""


class ResearchChatServiceError(Exception):
    """Raised when grounded research chat cannot be completed."""


async def generate_paper_manifest(
    paper_id: uuid.UUID,
    db: AsyncSession,
) -> ResearchManifest:
    """
    Generate and persist a structured ResearchManifest for a paper.

    Gemini receives selected markdown chunks only. The result is validated
    against app.schemas.research.ResearchManifest before it is stored in
    papers.research_manifest.
    """
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise PaperManifestNotFoundError(f"Paper {paper_id} was not found.")

    chunks = await _fetch_manifest_chunks(paper_id=paper_id, db=db)
    if not chunks:
        raise PaperManifestSourceError(
            f"Paper {paper_id} has no chunks. Run POST /api/v1/research/{paper_id}/chunk first."
        )

    context_chunks = _prepare_context_chunks(chunks)
    fallback_manifest = _build_fallback_manifest(paper=paper, context_chunks=context_chunks)

    manifest = fallback_manifest
    settings = get_settings()
    if _has_real_api_key(settings.gemini_api_key):
        try:
            manifest = await _generate_manifest_with_gemini(
                paper=paper,
                context_chunks=context_chunks,
                api_key=settings.gemini_api_key,
            )
        except Exception as exc:
            logger.exception(
                "Gemini manifest generation failed for paper_id=%s; using fallback: %s",
                paper_id,
                exc,
            )
    else:
        logger.warning("Gemini API key not configured. Using fallback research manifest.")

    try:
        paper.research_manifest = manifest.model_dump(mode="json")
        await db.commit()
        await db.refresh(paper)
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Failed to persist ResearchManifest for paper_id=%s: %s", paper_id, exc)
        raise ResearchManifestStorageError(
            f"Failed to persist ResearchManifest for paper {paper_id}."
        ) from exc

    return manifest


async def answer_paper_question(paper_id: uuid.UUID, query: str) -> dict[str, Any]:
    """
    Answer a question using only retrieved chunks from one paper.

    The returned dict matches ResearchChatResponse. No chat history, memory,
    open-ended tools, or external RAG framework is used.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Query must not be empty.")

    try:
        retrieved_chunks = await retrieve_relevant_chunks(
            query=cleaned_query,
            paper_id=paper_id,
            limit=CHAT_RETRIEVAL_LIMIT,
        )
    except RetrievalServiceError as exc:
        raise ResearchChatServiceError(f"Could not retrieve chunks for paper {paper_id}.") from exc

    if not retrieved_chunks:
        return ResearchChatResponse(
            answer=CANNOT_FIND_ANSWER,
            citations=[],
        ).model_dump(mode="json")

    fallback = ResearchChatResponse(
        answer=CANNOT_FIND_ANSWER,
        citations=[],
    )

    settings = get_settings()
    if not _has_real_api_key(settings.gemini_api_key):
        logger.warning("Gemini API key not configured. Returning grounded chat fallback.")
        return fallback.model_dump(mode="json")

    try:
        response = await _generate_chat_with_gemini(
            query=cleaned_query,
            retrieved_chunks=retrieved_chunks,
            api_key=settings.gemini_api_key,
        )
    except Exception as exc:
        logger.exception(
            "Gemini grounded chat failed for paper_id=%s; returning fallback: %s",
            paper_id,
            exc,
        )
        response = fallback

    return response.model_dump(mode="json")


async def _fetch_manifest_chunks(
    *,
    paper_id: uuid.UUID,
    db: AsyncSession,
) -> list[PaperChunk]:
    """Fetch the first bounded set of chunks for manifest generation."""
    stmt = (
        select(PaperChunk)
        .where(PaperChunk.paper_id == paper_id)
        .order_by(PaperChunk.chunk_index.asc())
        .limit(MAX_CONTEXT_CHUNKS)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _generate_manifest_with_gemini(
    *,
    paper: Paper,
    context_chunks: list[dict[str, Any]],
    api_key: str,
) -> ResearchManifest:
    """Call Gemini directly and validate the strict JSON response."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            import google.generativeai as genai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("google-generativeai is not installed.") from exc

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MANIFEST_MODEL)
    prompt = _build_manifest_prompt(paper=paper, context_chunks=context_chunks)

    response = await _generate_with_schema_fallback(model=model, prompt=prompt)
    response_text = _extract_response_text(response)
    manifest = _parse_manifest_response(response_text=response_text)
    return _normalize_manifest_contract(
        manifest=manifest,
        paper=paper,
        context_chunks=context_chunks,
    )


async def _generate_chat_with_gemini(
    *,
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    api_key: str,
) -> ResearchChatResponse:
    """Call Gemini for a strict grounded chat JSON response."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            import google.generativeai as genai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("google-generativeai is not installed.") from exc

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        GEMINI_MANIFEST_MODEL,
        system_instruction=CHAT_SYSTEM_INSTRUCTION,
    )
    prompt = _build_chat_prompt(query=query, retrieved_chunks=retrieved_chunks)

    response = await _generate_chat_with_schema_fallback(model=model, prompt=prompt)
    response_text = _extract_response_text(response)
    parsed = _parse_chat_response(response_text=response_text)
    return _normalize_chat_response(response=parsed, retrieved_chunks=retrieved_chunks)


async def _generate_with_schema_fallback(model: Any, prompt: str) -> Any:
    """Ask Gemini for strict JSON, retrying without response_schema if needed."""
    generation_config: dict[str, Any] = {
        "temperature": 0.1,
        "top_p": 0.85,
        "max_output_tokens": 1800,
        "response_mime_type": "application/json",
        "response_schema": _gemini_response_schema(),
    }

    try:
        return await model.generate_content_async(prompt, generation_config=generation_config)
    except TypeError as exc:
        logger.warning("Gemini SDK rejected response_schema; retrying JSON mode only: %s", exc)
    except Exception as exc:
        message = str(exc).lower()
        if not any(token in message for token in ("schema", "generation_config")):
            if any(token in message for token in ("quota", "rate", "429", "resource exhausted")):
                raise RuntimeError(f"Gemini rate limit or quota error: {exc}") from exc
            raise
        logger.warning("Gemini API rejected structured schema; retrying JSON mode only: %s", exc)

    generation_config.pop("response_schema", None)
    return await model.generate_content_async(prompt, generation_config=generation_config)


async def _generate_chat_with_schema_fallback(model: Any, prompt: str) -> Any:
    """Ask Gemini for strict chat JSON, retrying without response_schema if needed."""
    generation_config: dict[str, Any] = {
        "temperature": 0.0,
        "top_p": 0.8,
        "max_output_tokens": 900,
        "response_mime_type": "application/json",
        "response_schema": _chat_response_schema(),
    }

    try:
        return await model.generate_content_async(prompt, generation_config=generation_config)
    except TypeError as exc:
        logger.warning("Gemini SDK rejected chat response_schema; retrying JSON mode only: %s", exc)
    except Exception as exc:
        message = str(exc).lower()
        if not any(token in message for token in ("schema", "generation_config")):
            if any(token in message for token in ("quota", "rate", "429", "resource exhausted")):
                raise RuntimeError(f"Gemini rate limit or quota error: {exc}") from exc
            raise
        logger.warning("Gemini API rejected chat schema; retrying JSON mode only: %s", exc)

    generation_config.pop("response_schema", None)
    return await model.generate_content_async(prompt, generation_config=generation_config)


def _build_manifest_prompt(
    *,
    paper: Paper,
    context_chunks: list[dict[str, Any]],
) -> str:
    """Build a strict prompt for the ResearchManifest Knowledge Object."""
    source_context = {
        "paper_id": str(paper.id),
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "total_pages": paper.total_pages,
        "chunks": context_chunks,
    }
    schema_json = json.dumps(_gemini_response_schema(), ensure_ascii=True, sort_keys=True)
    context_json = json.dumps(source_context, ensure_ascii=True, sort_keys=True)

    return f"""
You are an academic researcher creating a structured Knowledge Object for DevTwin.

Use ONLY the supplied paper metadata and markdown chunks. Do not invent facts.
Do not write a prose answer. Do not include markdown fences. Return one JSON
object only, matching this schema exactly:
{schema_json}

Field rules:
- paper_id must exactly equal "{paper.id}".
- title must be the supplied paper title or null.
- core_concepts should be concise noun phrases suitable as future graph nodes.
- methodology_summary should be one short paragraph.
- key_findings must contain 3 to 5 concise findings supported by the chunks.
- tech_stack_mentions should include tools, frameworks, datasets, libraries, or platforms named in the chunks.
- generated_from_chunk_indices must contain only indices from the supplied chunks.
- source_chunk_count must equal {len(context_chunks)}.
- limitations should mention if the context is partial or insufficient; otherwise use null.

Paper context:
{context_json}
""".strip()


def _build_chat_prompt(
    *,
    query: str,
    retrieved_chunks: list[dict[str, Any]],
) -> str:
    """Build a bounded context prompt for grounded research chat."""
    context_block = _format_chat_context(retrieved_chunks)
    allowed_indices = _allowed_chunk_indices(retrieved_chunks)
    schema_json = json.dumps(_chat_response_schema(), ensure_ascii=True, sort_keys=True)

    return f"""
User query:
{query}

Retrieved chunks:
{context_block}

Return one JSON object only, matching this schema exactly:
{schema_json}

Grounding rules:
- Answer using ONLY the retrieved chunks above.
- If the answer is not explicitly supported by the chunks, set answer to exactly:
  "{CANNOT_FIND_ANSWER}"
- citations must be a list of chunk indices used as direct evidence.
- citations may only include these chunk indices: {allowed_indices}
- If answer is "{CANNOT_FIND_ANSWER}", citations must be [].
- Do not cite a chunk unless its text directly supports the answer.
- Do not include markdown fences or extra keys.
""".strip()


def _gemini_response_schema() -> dict[str, Any]:
    """Minimal JSON schema compatible with Gemini structured outputs."""
    return {
        "type": "object",
        "properties": {
            "manifest_version": {"type": "string"},
            "paper_id": {"type": "string"},
            "title": {"type": "string", "nullable": True},
            "core_concepts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "methodology_summary": {"type": "string"},
            "key_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "tech_stack_mentions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "generated_from_chunk_indices": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "source_chunk_count": {"type": "integer"},
            "limitations": {"type": "string", "nullable": True},
        },
        "required": [
            "manifest_version",
            "paper_id",
            "title",
            "core_concepts",
            "methodology_summary",
            "key_findings",
            "tech_stack_mentions",
            "generated_from_chunk_indices",
            "source_chunk_count",
            "limitations",
        ],
    }


def _chat_response_schema() -> dict[str, Any]:
    """Minimal JSON schema for grounded chat answers."""
    return {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Grounded answer or exact fallback when unsupported.",
            },
            "citations": {
                "type": "array",
                "description": "Exact chunk_index values used as evidence.",
                "items": {"type": "integer"},
            },
        },
        "required": ["answer", "citations"],
    }


def _parse_manifest_response(response_text: str) -> ResearchManifest:
    """Parse and validate Gemini JSON as ResearchManifest."""
    cleaned = _strip_json_fence(response_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}") from exc

    try:
        return ResearchManifest.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Gemini JSON did not match ResearchManifest schema: {exc}") from exc


def _parse_chat_response(response_text: str) -> ResearchChatResponse:
    """Parse and validate Gemini JSON as ResearchChatResponse."""
    cleaned = _strip_json_fence(response_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid chat JSON: {exc}") from exc

    try:
        return ResearchChatResponse.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Gemini JSON did not match ResearchChatResponse schema: {exc}") from exc


def _normalize_manifest_contract(
    *,
    manifest: ResearchManifest,
    paper: Paper,
    context_chunks: list[dict[str, Any]],
) -> ResearchManifest:
    """Keep DB-owned manifest fields stable even if Gemini varies harmless values."""
    allowed_indices = {
        int(chunk["chunk_index"])
        for chunk in context_chunks
        if isinstance(chunk.get("chunk_index"), int)
    }
    normalized_indices = [
        index
        for index in manifest.generated_from_chunk_indices
        if index in allowed_indices
    ]
    if not normalized_indices:
        normalized_indices = sorted(allowed_indices)

    return manifest.model_copy(
        update={
            "manifest_version": "research-manifest/v1",
            "paper_id": paper.id,
            "title": paper.title,
            "generated_from_chunk_indices": normalized_indices,
            "source_chunk_count": len(context_chunks),
        }
    )


def _normalize_chat_response(
    *,
    response: ResearchChatResponse,
    retrieved_chunks: list[dict[str, Any]],
) -> ResearchChatResponse:
    """Ensure citations are exact retrieved chunk indices and answers remain grounded."""
    allowed_indices = set(_allowed_chunk_indices(retrieved_chunks))
    normalized_citations: list[int] = []
    seen: set[int] = set()

    for citation in response.citations:
        if citation in allowed_indices and citation not in seen:
            normalized_citations.append(citation)
            seen.add(citation)

    answer = response.answer.strip() or CANNOT_FIND_ANSWER
    if answer.casefold() == CANNOT_FIND_ANSWER.casefold():
        return ResearchChatResponse(answer=CANNOT_FIND_ANSWER, citations=[])

    if not normalized_citations:
        logger.warning("Gemini returned a non-fallback answer without valid citations.")
        return ResearchChatResponse(answer=CANNOT_FIND_ANSWER, citations=[])

    return ResearchChatResponse(answer=answer, citations=normalized_citations)


def _extract_response_text(response: Any) -> str:
    """Extract text from google-generativeai responses across SDK variants."""
    try:
        text = response.text
    except Exception:
        text = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                text = "".join(getattr(part, "text", "") for part in parts)

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Gemini returned an empty response.")
    return text.strip()


def _prepare_context_chunks(chunks: list[PaperChunk]) -> list[dict[str, Any]]:
    """Trim chunks into a bounded prompt context."""
    prepared: list[dict[str, Any]] = []
    used_chars = 0

    for chunk in chunks:
        content = (chunk.content or "").strip()
        if not content:
            continue

        trimmed = content[:MAX_CHUNK_CHARS]
        if used_chars + len(trimmed) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - used_chars
            if remaining <= 0:
                break
            trimmed = trimmed[:remaining]

        prepared.append(
            {
                "chunk_index": chunk.chunk_index,
                "text": trimmed,
            }
        )
        used_chars += len(trimmed)

    return prepared


def _format_chat_context(retrieved_chunks: list[dict[str, Any]]) -> str:
    """Format retrieved chunks as citation-addressable source text."""
    blocks: list[str] = []
    for chunk in retrieved_chunks:
        chunk_index = chunk.get("chunk_index")
        text = str(chunk.get("text_content") or "").strip()
        if chunk_index is None or not text:
            continue
        blocks.append(f"[Chunk {chunk_index}]\n{text[:MAX_CHUNK_CHARS]}")
    return "\n\n".join(blocks)


def _allowed_chunk_indices(retrieved_chunks: list[dict[str, Any]]) -> list[int]:
    """Return sorted unique integer chunk indices from retrieval results."""
    indices = {
        int(chunk["chunk_index"])
        for chunk in retrieved_chunks
        if isinstance(chunk.get("chunk_index"), int)
    }
    return sorted(indices)


def _build_fallback_manifest(
    *,
    paper: Paper,
    context_chunks: list[dict[str, Any]],
) -> ResearchManifest:
    """Build a safe schema-valid fallback when Gemini is unavailable."""
    combined_text = " ".join(str(chunk.get("text", "")) for chunk in context_chunks)
    concepts = _fallback_concepts(paper=paper, text=combined_text)
    findings = _fallback_findings(text=combined_text)
    indices = [
        int(chunk["chunk_index"])
        for chunk in context_chunks
        if isinstance(chunk.get("chunk_index"), int)
    ]

    methodology = (
        "The manifest was generated from selected parsed markdown chunks. "
        "The available context describes the research content but may not include the full paper."
    )
    if paper.abstract:
        methodology = f"{methodology} Abstract signal: {paper.abstract[:500]}"

    return ResearchManifest(
        manifest_version="research-manifest/v1",
        paper_id=paper.id,
        title=paper.title,
        core_concepts=concepts,
        methodology_summary=methodology,
        key_findings=findings,
        tech_stack_mentions=_fallback_tech_mentions(combined_text),
        generated_from_chunk_indices=indices,
        source_chunk_count=len(context_chunks),
        limitations="Generated with deterministic fallback because Gemini was unavailable or returned invalid JSON.",
    )


def _fallback_concepts(*, paper: Paper, text: str) -> list[str]:
    """Infer a minimal concept list without LLM generation."""
    candidates: list[str] = []
    for value in [paper.title, paper.abstract]:
        if value:
            candidates.extend(_candidate_phrases(value))
    candidates.extend(_candidate_phrases(text))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if key not in seen and len(candidate) > 3:
            deduped.append(candidate[:80])
            seen.add(key)
        if len(deduped) >= 6:
            break

    return deduped or ["Research Paper"]


def _fallback_findings(text: str) -> list[str]:
    """Create three conservative findings from available source text."""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.strip()) >= 20
    ]
    findings = [sentence[:240] for sentence in sentences[:5]]
    while len(findings) < 3:
        findings.append("The supplied chunks do not provide enough evidence for a detailed finding.")
    return findings[:5]


def _fallback_tech_mentions(text: str) -> list[str]:
    """Extract obvious tool/framework mentions with deterministic matching."""
    known_terms = [
        "Python",
        "PyTorch",
        "TensorFlow",
        "FastAPI",
        "Qdrant",
        "Vector Search",
        "Reinforcement Learning",
        "Transformer",
        "BERT",
        "LLM",
        "RAG",
        "Markdown",
        "PDF",
    ]
    lower_text = text.casefold()
    return [term for term in known_terms if term.casefold() in lower_text][:15]


def _candidate_phrases(text: str) -> list[str]:
    """Extract simple title-case and technical phrases for fallback concepts."""
    phrases = re.findall(r"\b[A-Z][A-Za-z0-9+-]*(?:\s+[A-Z][A-Za-z0-9+-]*){0,3}\b", text)
    if not phrases:
        words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9+-]{4,}\b", text)
        phrases = words[:12]
    return [phrase.strip() for phrase in phrases if phrase.strip()]


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    return match.group(1).strip() if match else stripped


def _has_real_api_key(value: str | None) -> bool:
    if not value:
        return False
    stripped = value.strip()
    placeholders = {
        "your_gemini_api_key_here",
        "your_google_api_key_here",
        "AIzaSy_your_key_here",
    }
    return stripped not in placeholders and not stripped.lower().startswith("your_")
