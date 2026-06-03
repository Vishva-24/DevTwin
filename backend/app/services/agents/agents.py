"""
app/services/agents/agents.py

Stateless asynchronous LangGraph nodes for DevTwin orchestration.
"""

import logging
import uuid
from functools import lru_cache
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.research import Paper
from app.services.agents.state import AgentState
from app.services.graph.graph_service import GraphService
from app.services.research.embedding_service import EmbeddingGenerationError, embed_query
from app.services.research.qdrant_service import QdrantServiceError, search_chunks

logger = logging.getLogger(__name__)

DEFAULT_AGENT_USER_ID = "me"
RESEARCH_AGENT_RESULT_LIMIT = 5
MAX_RESEARCH_CHUNK_CHARS = 1_200
NEXT_KNOWLEDGE = "knowledge"
NEXT_CODE = "code"
NEXT_RESEARCH = "research"
NEXT_SYNTHESIZE = "synthesize"
PLANNER_PRIMARY_MODEL = "gemini-1.5-flash"
PLANNER_FALLBACK_MODEL = "gemini-2.5-flash"
SYNTHESIZER_PRIMARY_MODEL = "gemini-1.5-pro"
SYNTHESIZER_FALLBACK_MODEL = "gemini-2.5-flash"
_UNAVAILABLE_PLANNER_MODELS: set[str] = set()
_UNAVAILABLE_SYNTHESIZER_MODELS: set[str] = set()

_PLANNER_SYSTEM_PROMPT = """
You are the orchestration brain of a Digital Twin. Read the user's query and their current missing skills. Decide the single most important next step to gather context. If you need their personal graph/history, choose 'knowledge'. If you need repository architecture, choose 'code'. If you need academic literature, choose 'research'. If you have enough context to answer the user, choose 'synthesize'.
""".strip()

_SYNTHESIZER_SYSTEM_PROMPT = """
You are the final synthesis brain of a Digital Twin. Your goal is to answer the user's query by perfectly blending their personal skills (Graph Context), their codebase architectures (Code Context), and academic literature (Research Context). You must explicitly cite the research sources provided in the context when making claims. Do not hallucinate capabilities; rely strictly on the provided state.
""".strip()


class PlannerDecision(BaseModel):
    """Structured routing decision returned by the planner LLM."""

    model_config = ConfigDict(extra="forbid")

    next_step: Literal["knowledge", "code", "research", "synthesize"] = Field(
        description=(
            "The single next routing step. Must be exactly one of: "
            "'knowledge', 'code', 'research', or 'synthesize'."
        ),
    )


async def planner_agent(state: AgentState) -> dict[str, Any]:
    """
    Route the workflow with Gemini structured output.

    Gemini decides which context source matters most next. A deterministic
    fallback keeps the graph usable in local development if the API is not
    configured or temporarily unavailable.
    """
    try:
        next_step = await _invoke_planner_llm(state)
    except Exception as exc:
        logger.warning("Gemini planner failed; using deterministic fallback: %s", exc)
        next_step = _deterministic_planner_decision(state)

    logger.info("Planner routed DevTwin agent workflow to next_step=%s.", next_step)
    return {"next_step": next_step}


async def _invoke_planner_llm(state: AgentState) -> str:
    """Invoke Gemini planner structured output with model fallback."""
    messages = [
        SystemMessage(content=_PLANNER_SYSTEM_PROMPT),
        HumanMessage(content=_build_planner_prompt(state)),
    ]
    last_error: Exception | None = None

    for model_name in _planner_model_candidates():
        if model_name in _UNAVAILABLE_PLANNER_MODELS:
            continue

        try:
            planner = _get_planner_llm(model_name)
            decision = await planner.ainvoke(messages)
            parsed_decision = (
                decision
                if isinstance(decision, PlannerDecision)
                else PlannerDecision.model_validate(decision)
            )
            logger.info("Gemini planner routed using model=%s.", model_name)
            return parsed_decision.next_step
        except Exception as exc:
            last_error = exc
            if _should_skip_model_for_process(exc):
                _UNAVAILABLE_PLANNER_MODELS.add(model_name)
            logger.warning("Gemini planner model=%s failed: %s", model_name, exc)

    raise RuntimeError("All Gemini planner model attempts failed.") from last_error


@lru_cache(maxsize=4)
def _get_planner_llm(model_name: str) -> Any:
    """Return a cached Gemini planner runnable with structured output enabled."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini API key is not configured.")

    llm = ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0,
        api_key=settings.gemini_api_key,
        retries=0,
        request_timeout=15,
    )
    return llm.with_structured_output(PlannerDecision)


def _planner_model_candidates() -> tuple[str, ...]:
    """Return primary and fallback Gemini planner models without duplicates."""
    settings = get_settings()
    candidates = [PLANNER_PRIMARY_MODEL]

    if settings.gemini_model and settings.gemini_model not in candidates:
        candidates.append(settings.gemini_model)
    if PLANNER_FALLBACK_MODEL not in candidates:
        candidates.append(PLANNER_FALLBACK_MODEL)

    return tuple(candidates)


def _should_skip_model_for_process(exc: Exception) -> bool:
    """Return True for errors unlikely to recover during the current process."""
    message = str(exc).upper()
    return "NOT_FOUND" in message or "RESOURCE_EXHAUSTED" in message


async def _invoke_synthesizer_llm(state: AgentState) -> str:
    """Invoke Gemini to synthesize the final answer from gathered state context."""
    messages = [
        SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
        HumanMessage(content=_build_synthesizer_prompt(state)),
    ]
    last_error: Exception | None = None

    for model_name in _synthesizer_model_candidates():
        if model_name in _UNAVAILABLE_SYNTHESIZER_MODELS:
            continue

        try:
            llm = _get_synthesizer_llm(model_name)
            response = await llm.ainvoke(messages)
            content = _message_content_to_text(response.content)
            if not content:
                raise RuntimeError("Gemini synthesizer returned an empty response.")

            logger.info("Gemini synthesizer generated response using model=%s.", model_name)
            return content
        except Exception as exc:
            last_error = exc
            if _should_skip_model_for_process(exc):
                _UNAVAILABLE_SYNTHESIZER_MODELS.add(model_name)
            logger.warning("Gemini synthesizer model=%s failed: %s", model_name, exc)

    raise RuntimeError("All Gemini synthesizer model attempts failed.") from last_error


@lru_cache(maxsize=4)
def _get_synthesizer_llm(model_name: str) -> ChatGoogleGenerativeAI:
    """Return a cached Gemini synthesizer model."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini API key is not configured.")

    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0.2,
        api_key=settings.gemini_api_key,
        retries=0,
        request_timeout=30,
    )


def _synthesizer_model_candidates() -> tuple[str, ...]:
    """Return primary and fallback Gemini synthesizer models without duplicates."""
    settings = get_settings()
    candidates = [SYNTHESIZER_PRIMARY_MODEL]

    if settings.gemini_model and settings.gemini_model not in candidates:
        candidates.append(settings.gemini_model)
    if SYNTHESIZER_FALLBACK_MODEL not in candidates:
        candidates.append(SYNTHESIZER_FALLBACK_MODEL)

    return tuple(candidates)


def _build_synthesizer_prompt(state: AgentState) -> str:
    """Format all gathered AgentState context into one synthesis prompt."""
    return "\n\n".join(
        [
            "User Query:\n" + (state.get("user_query", "").strip() or "No user query provided."),
            "Current Missing Skills:\n" + _format_context_block(state.get("skill_gaps", [])),
            "Graph Context:\n" + _format_context_block(state.get("graph_context", [])),
            "Code Context:\n" + _format_context_block(state.get("code_context", [])),
            "Research Context:\n" + _format_context_block(state.get("research_context", [])),
            (
                "Answering rules:\n"
                "- Answer the user directly and personally.\n"
                "- Blend graph, code, and research context when available.\n"
                "- Cite research claims using the exact [Source: ...] labels from Research Context.\n"
                "- If a needed fact is absent from the provided state, say that it is not available."
            ),
        ]
    )


def _format_context_block(values: list[str]) -> str:
    """Format context lists for synthesis, using a required empty-state phrase."""
    if not values:
        return "None available."

    return "\n\n".join(str(value) for value in values if str(value).strip()) or "None available."


def _message_content_to_text(content: Any) -> str:
    """Normalize LangChain message content into plain text."""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    return str(content).strip() if content is not None else ""


def _build_planner_prompt(state: AgentState) -> str:
    """Build the human prompt payload for the planner LLM."""
    return "\n".join(
        [
            "User query:",
            state.get("user_query", "").strip() or "No user query provided.",
            "",
            "Current missing skills:",
            _format_list_for_prompt(state.get("skill_gaps", [])),
            "",
            "Existing graph context:",
            _format_list_for_prompt(state.get("graph_context", [])),
            "",
            "Existing code context:",
            _format_list_for_prompt(state.get("code_context", [])),
            "",
            "Existing research context:",
            _format_list_for_prompt(state.get("research_context", [])),
            "",
            "Return only the structured PlannerDecision.",
        ]
    )


def _format_list_for_prompt(values: list[str]) -> str:
    """Format state lists compactly for the planner prompt."""
    if not values:
        return "- None"

    return "\n".join(f"- {value}" for value in values)


def _deterministic_planner_decision(state: AgentState) -> str:
    """Fallback router used only when Gemini cannot produce a decision."""
    user_query = state.get("user_query", "")
    skill_gaps = state.get("skill_gaps", [])
    query_lower = user_query.lower()

    needs_graph = bool(skill_gaps) or _contains_any(
        query_lower,
        ("graph", "skill", "gap", "concept", "technology", "framework", "paper", "repository"),
    )
    needs_code = _contains_any(
        query_lower,
        ("code", "repo", "repository", "implementation", "api", "backend", "frontend", "bug"),
    )
    needs_research = bool(skill_gaps) or _contains_any(
        query_lower,
        ("research", "paper", "pdf", "citation", "chunk", "methodology", "finding"),
    )

    if needs_graph and not state.get("graph_context"):
        next_step = NEXT_KNOWLEDGE
    elif needs_code and not state.get("code_context"):
        next_step = NEXT_CODE
    elif needs_research and not state.get("research_context"):
        next_step = NEXT_RESEARCH
    else:
        next_step = NEXT_SYNTHESIZE

    return next_step


async def knowledge_agent(state: AgentState) -> dict[str, Any]:
    """Fetch and summarize the user's Personal Learning Graph from Neo4j."""
    graph_service = GraphService()

    try:
        async with graph_service.driver.session() as session:
            result = await session.run(
                _USER_OUTGOING_RELATIONSHIPS_QUERY,
                user_id=DEFAULT_AGENT_USER_ID,
            )
            rows = await result.data()

        context = _format_user_graph_summary(rows, DEFAULT_AGENT_USER_ID)
        logger.info(
            "Knowledge agent appended Neo4j graph context with %d relationship row(s).",
            len(rows),
        )
    except Exception as exc:
        logger.exception("Knowledge agent failed to query Neo4j graph context: %s", exc)
        context = "Graph context unavailable due to database error."
    finally:
        await graph_service.close()

    return {
        "graph_context": _append_context(state, "graph_context", context),
        "messages": [AIMessage(content="Knowledge agent collected graph context.")],
    }


async def code_agent(state: AgentState) -> dict[str, Any]:
    """Fetch and summarize owned repository tech stacks from Neo4j."""
    graph_service = GraphService()

    try:
        async with graph_service.driver.session() as session:
            result = await session.run(
                _USER_REPOSITORY_TECH_QUERY,
                user_id=DEFAULT_AGENT_USER_ID,
            )
            rows = await result.data()

        context = _format_codebase_summary(rows, DEFAULT_AGENT_USER_ID)
        logger.info(
            "Code agent appended Neo4j code context with %d repository tech row(s).",
            len(rows),
        )
    except Exception as exc:
        logger.exception("Code agent failed to query Neo4j repository context: %s", exc)
        context = "Codebase context unavailable."
    finally:
        await graph_service.close()

    return {
        "code_context": _append_context(state, "code_context", context),
        "messages": [AIMessage(content="Code agent collected code context.")],
    }


async def research_agent(state: AgentState) -> dict[str, Any]:
    """Retrieve semantically relevant research chunks from local Qdrant."""
    try:
        query = state.get("user_query", "").strip()
        if not query:
            raise ValueError("Cannot retrieve research context without a user query.")

        results = await _search_research_chunks(query=query, limit=RESEARCH_AGENT_RESULT_LIMIT)
        context = await _format_research_context(results)

        logger.info("Research agent appended %d retrieved research chunk(s).", len(results))
    except Exception as exc:
        logger.exception("Research agent failed to retrieve Qdrant context: %s", exc)
        context = "No relevant academic research found for this query."

    return {
        "research_context": _append_context(state, "research_context", context),
        "messages": [AIMessage(content="Research agent collected research context.")],
    }


async def synthesizer_agent(state: AgentState) -> dict[str, Any]:
    """Generate the final personalized Digital Twin response with Gemini."""
    try:
        response = await _invoke_synthesizer_llm(state)
    except Exception as exc:
        logger.warning("Gemini synthesizer failed; using deterministic fallback: %s", exc)
        response = _build_synthesis_response(state)

    logger.info("Synthesizer agent produced final response.")
    return {"messages": [("ai", response)]}


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """Return True when any routing keyword is present in text."""
    return any(term in text for term in terms)


def _format_user_graph_summary(rows: list[dict[str, Any]], fallback_user_id: str) -> str:
    """Format outgoing User graph relationships into a compact text summary."""
    if not rows:
        return f"User '{fallback_user_id}' has no outgoing relationships in the graph."

    first_row = rows[0]
    user_id = str(first_row.get("user_id") or fallback_user_id)
    user_name = str(first_row.get("user_name") or "Developer")
    relationship_sentences: list[str] = []
    seen_sentences: set[str] = set()

    for row in rows:
        relationship_type = row.get("relationship_type")
        target_display = row.get("target_display")

        if not relationship_type or not target_display:
            continue

        sentence = _format_relationship_sentence(user_id, relationship_type, row)
        if sentence in seen_sentences:
            continue

        seen_sentences.add(sentence)
        relationship_sentences.append(sentence)

    if not relationship_sentences:
        return f"User '{user_id}' ({user_name}) exists but has no outgoing graph relationships."

    return "\n".join(
        [
            f"User graph summary for '{user_id}' ({user_name}):",
            *relationship_sentences,
        ]
    )


def _format_relationship_sentence(
    user_id: str,
    relationship_type: str,
    row: dict[str, Any],
) -> str:
    """Format one supported User outgoing relationship as a readable sentence."""
    target_display = str(row.get("target_display"))
    target_label = _primary_target_label(row.get("target_labels"))

    if relationship_type == "HAS_SKILL":
        sentence = f"User '{user_id}' has skill '{target_display}'"
        confidence = _as_float(row.get("confidence"))
        strength_score = _as_float(row.get("strength_score"))

        if confidence is not None:
            sentence += f" with confidence {confidence:.2f}"
        if strength_score is not None:
            sentence += f" and strength score {strength_score:.0f}"
        return f"{sentence}."

    if relationship_type == "OWNER_OF":
        return f"User '{user_id}' owns repository '{target_display}'."

    if relationship_type == "READ":
        return f"User '{user_id}' read paper '{target_display}'."

    return (
        f"User '{user_id}' has outgoing relationship '{relationship_type}' "
        f"to {target_label} '{target_display}'."
    )


def _format_codebase_summary(rows: list[dict[str, Any]], fallback_user_id: str) -> str:
    """Format owned repositories and their USES relationships into code context."""
    repositories: dict[str, dict[str, Any]] = {}

    for row in rows:
        repository_id = row.get("repository_id")
        repository_name = row.get("repository_name")
        if not repository_id and not repository_name:
            continue

        repo_key = str(repository_id or repository_name)
        repository = repositories.setdefault(
            repo_key,
            {
                "name": str(repository_name or repository_id),
                "github_url": row.get("github_url"),
                "frameworks": set(),
                "technologies": set(),
            },
        )

        tech_name = row.get("tech_name")
        if not tech_name:
            continue

        cleaned_tech_name = _clean_tech_name(str(tech_name))
        if cleaned_tech_name is None:
            continue

        target_bucket = (
            "frameworks"
            if "Framework" in {str(label) for label in row.get("tech_labels") or []}
            else "technologies"
        )
        repository[target_bucket].add(cleaned_tech_name)

    if not repositories:
        return f"User '{fallback_user_id}' has no owned repository architecture data in Neo4j."

    lines = [f"Repository architecture summary for user '{fallback_user_id}':"]
    for repository in sorted(repositories.values(), key=lambda item: item["name"].lower()):
        frameworks = sorted(repository["frameworks"])
        technologies = sorted(repository["technologies"])
        stack_items = sorted({*frameworks, *technologies}, key=str.lower)
        stack_summary = ", ".join(stack_items) if stack_items else "no linked tech stack nodes"
        source = f" ({repository['github_url']})" if repository.get("github_url") else ""
        lines.append(
            f"Repository '{repository['name']}'{source} uses the following tech stack: "
            f"{stack_summary}."
        )

    return "\n".join(lines)


def _clean_tech_name(raw_name: str) -> str | None:
    """Normalize noisy parser-derived graph node names into readable tech labels."""
    name = raw_name.strip()
    if not name:
        return None

    lowered = name.lower()
    if lowered.startswith("path hint:"):
        return None

    noisy_prefixes = (
        "framework ",
        "language ",
        "infra tool ",
        "package manager ",
        "database ",
        "ai tool ",
        "tool ",
    )
    if " detected" in lowered and lowered.startswith(noisy_prefixes):
        parts = name.split("'")
        if len(parts) >= 3 and parts[1].strip():
            return parts[1].strip()

    if len(name) > 80:
        return None

    return name


def _primary_target_label(labels: Any) -> str:
    """Return a stable display label from Neo4j labels."""
    if not isinstance(labels, list) or not labels:
        return "node"

    preferred_order = ("Repository", "Paper", "Technology", "Framework", "Concept")
    label_set = {str(label) for label in labels}
    for label in preferred_order:
        if label in label_set:
            return label

    return sorted(label_set)[0]


def _as_float(value: Any) -> float | None:
    """Coerce Neo4j numeric values into floats for formatting."""
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _search_research_chunks(query: str, limit: int) -> list[dict[str, Any]]:
    """Embed a query and search all research paper chunks in Qdrant."""
    try:
        query_vector = await embed_query(query)
        return await search_chunks(query_vector=query_vector, paper_id=None, limit=limit)
    except (EmbeddingGenerationError, QdrantServiceError) as exc:
        raise RuntimeError("Research vector retrieval failed.") from exc


async def _format_research_context(results: list[dict[str, Any]]) -> str:
    """Format Qdrant search results into citation-ready research context."""
    if not results:
        return "No relevant academic research found for this query."

    paper_titles = await _fetch_paper_titles(_paper_ids_from_results(results))
    chunks: list[str] = ["Relevant academic research chunks:"]

    for index, result in enumerate(results, start=1):
        paper_id = str(result.get("paper_id") or "unknown-paper")
        title = paper_titles.get(paper_id) or f"Paper {paper_id}"
        chunk_index = result.get("chunk_index")
        score = _as_float(result.get("score"))
        text = _truncate_text(str(result.get("text_content") or ""))
        score_suffix = f", score {score:.3f}" if score is not None else ""

        chunks.append(
            "\n".join(
                [
                    f"{index}. [Source: \"{title}\", chunk {chunk_index}{score_suffix}]",
                    text or "No chunk text was returned.",
                ]
            )
        )

    return "\n\n".join(chunks)


async def _fetch_paper_titles(paper_ids: list[uuid.UUID]) -> dict[str, str]:
    """Fetch paper titles for citation labels from PostgreSQL."""
    if not paper_ids:
        return {}

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Paper.id, Paper.title, Paper.file_name).where(Paper.id.in_(paper_ids))
            )
            rows = result.all()
    except Exception as exc:
        logger.warning("Failed to enrich research citations with paper titles: %s", exc)
        return {}

    titles: dict[str, str] = {}
    for paper_id, title, file_name in rows:
        titles[str(paper_id)] = str(title or file_name or paper_id)

    return titles


def _paper_ids_from_results(results: list[dict[str, Any]]) -> list[uuid.UUID]:
    """Extract valid UUID paper ids from Qdrant payloads."""
    paper_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    for result in results:
        raw_paper_id = result.get("paper_id")
        if raw_paper_id is None:
            continue

        try:
            paper_id = uuid.UUID(str(raw_paper_id))
        except ValueError:
            continue

        if paper_id in seen:
            continue

        seen.add(paper_id)
        paper_ids.append(paper_id)

    return paper_ids


def _truncate_text(text: str) -> str:
    """Keep retrieved chunks compact enough for downstream synthesis prompts."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= MAX_RESEARCH_CHUNK_CHARS:
        return cleaned

    return f"{cleaned[:MAX_RESEARCH_CHUNK_CHARS].rstrip()}..."


def _append_context(state: AgentState, key: str, value: str) -> list[str]:
    """Append a context item while preserving any values already in state."""
    existing = state.get(key, [])
    return [*existing, value]


def _build_synthesis_response(state: AgentState) -> str:
    """Build a compact response from available context blocks."""
    sections: list[str] = [
        "DevTwin orchestration scaffold completed.",
        f"User query: {state.get('user_query', '').strip() or 'No query provided.'}",
    ]

    skill_gaps = state.get("skill_gaps", [])
    if skill_gaps:
        sections.append(f"Skill gaps: {', '.join(skill_gaps)}")

    context_sections = (
        ("Graph context", state.get("graph_context", [])),
        ("Code context", state.get("code_context", [])),
        ("Research context", state.get("research_context", [])),
    )

    for title, context_items in context_sections:
        if context_items:
            formatted_items = "\n".join(f"- {item}" for item in context_items)
            sections.append(f"{title}:\n{formatted_items}")

    return "\n\n".join(sections)


_USER_OUTGOING_RELATIONSHIPS_QUERY = """
MATCH (user:User {id: $user_id})
OPTIONAL MATCH (user)-[relationship]->(target)
WHERE type(relationship) IN ["HAS_SKILL", "OWNER_OF", "READ"]
RETURN
  user.id AS user_id,
  coalesce(user.name, "Developer") AS user_name,
  type(relationship) AS relationship_type,
  labels(target) AS target_labels,
  coalesce(
    target.name,
    target.repo_name,
    target.title,
    target.filename,
    toString(target.id)
  ) AS target_display,
  relationship.confidence AS confidence,
  relationship.strength_score AS strength_score
ORDER BY relationship_type ASC, target_display ASC
"""


_USER_REPOSITORY_TECH_QUERY = """
MATCH (user:User {id: $user_id})
OPTIONAL MATCH (user)-[:OWNER_OF]->(repository:Repository)
OPTIONAL MATCH (repository)-[:USES]->(tech)
WHERE tech IS NULL OR tech:Technology OR tech:Framework
RETURN
  repository.id AS repository_id,
  coalesce(repository.repo_name, repository.github_url, toString(repository.id)) AS repository_name,
  repository.github_url AS github_url,
  labels(tech) AS tech_labels,
  tech.name AS tech_name
ORDER BY repository_name ASC, tech_name ASC
"""
