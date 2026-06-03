"""
app/services/agents/state.py

Shared LangGraph state for DevTwin multi-agent orchestration.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Strict shared state passed between DevTwin agent nodes."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    skill_gaps: list[str]
    graph_context: list[str]
    code_context: list[str]
    research_context: list[str]
    next_step: str
