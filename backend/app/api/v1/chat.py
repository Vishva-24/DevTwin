"""
app/api/v1/chat.py

FastAPI router for invoking the DevTwin LangGraph multi-agent pipeline.
"""

import logging
from typing import Any

from langchain_core.messages import BaseMessage
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.services.agents.graph import agent_graph
from app.services.graph.graph_service import GraphService, GraphServiceError
from app.services.twin.skill_service import SkillService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Multi-Agent Chat"])

DEFAULT_SKILL_GAPS = ["Docker", "MLOps"]


class ChatRequest(BaseModel):
    """Request body for invoking the multi-agent graph."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=4_000,
        description="User query to route through the DevTwin agent graph.",
    )


class ChatResponse(BaseModel):
    """Response returned by the multi-agent graph invocation."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., description="Final synthesized AI answer.")
    used_agents: list[str] = Field(
        default_factory=list,
        description="Names of specialist agents that populated context.",
    )
    context_snippets: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Raw context snippets retrieved by graph, code, and research agents.",
    )


@router.post(
    "/invoke",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Invoke the DevTwin multi-agent graph",
)
async def invoke_multi_agent_chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Invoke the compiled LangGraph agent workflow for one user query."""
    query = body.query.strip()
    skill_gaps = await _get_current_skill_gaps(request)

    initial_state = {
        "messages": [],
        "user_query": query,
        "skill_gaps": skill_gaps,
        "graph_context": [],
        "code_context": [],
        "research_context": [],
        "next_step": "",
    }

    try:
        response = await agent_graph.ainvoke(initial_state, {"recursion_limit": 12})
    except Exception as exc:
        logger.exception("LangGraph chat invocation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The multi-agent graph could not complete the request.",
        ) from exc

    return ChatResponse(
        answer=_extract_final_answer(response),
        used_agents=_used_agents(response),
        context_snippets={
            "graph": _string_list(response.get("graph_context")),
            "code": _string_list(response.get("code_context")),
            "research": _string_list(response.get("research_context")),
        },
    )


async def _get_current_skill_gaps(request: Request) -> list[str]:
    """Fetch current graph skill gaps, falling back to a stable mock list."""
    graph_service = getattr(request.app.state, "graph_service", None)
    graph_ready = bool(getattr(request.app.state, "graph_ready", False))

    if not graph_ready or not isinstance(graph_service, GraphService):
        return DEFAULT_SKILL_GAPS

    try:
        gap_response = await SkillService(graph_service).get_skill_gap(target_role="MLEngineer")
    except GraphServiceError as exc:
        logger.warning("Could not fetch skill gaps for chat; using fallback gaps: %s", exc)
        return DEFAULT_SKILL_GAPS

    missing = gap_response.get("missing", [])
    gaps = [
        str(item.get("name"))
        for item in missing
        if isinstance(item, dict) and item.get("name")
    ]
    return gaps or DEFAULT_SKILL_GAPS


def _used_agents(response: dict[str, Any]) -> list[str]:
    """Return human-readable specialist agent names that populated context."""
    agents: list[str] = []
    if _string_list(response.get("graph_context")):
        agents.append("Knowledge Agent")
    if _string_list(response.get("code_context")):
        agents.append("Code Agent")
    if _string_list(response.get("research_context")):
        agents.append("Research Agent")
    return agents


def _extract_final_answer(response: dict[str, Any]) -> str:
    """Extract the final synthesized message from LangGraph state."""
    messages = response.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return "No response was generated."

    return _message_to_text(messages[-1]) or "No response was generated."


def _message_to_text(message: Any) -> str:
    """Normalize a LangChain message, tuple message, or raw value into text."""
    if isinstance(message, BaseMessage):
        return _content_to_text(message.content)

    if isinstance(message, tuple) and len(message) >= 2:
        return _content_to_text(message[1])

    if isinstance(message, dict):
        return _content_to_text(message.get("content"))

    return str(message).strip() if message is not None else ""


def _content_to_text(content: Any) -> str:
    """Convert LangChain content payloads into plain text."""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()

    return str(content).strip() if content is not None else ""


def _string_list(value: Any) -> list[str]:
    """Normalize state context values into a list of strings."""
    if not isinstance(value, list):
        return []

    return [str(item) for item in value if str(item).strip()]
