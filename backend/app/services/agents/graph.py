"""
app/services/agents/graph.py

LangGraph workflow compilation for DevTwin multi-agent orchestration.
"""

import logging
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.agents.agents import (
    NEXT_CODE,
    NEXT_KNOWLEDGE,
    NEXT_RESEARCH,
    NEXT_SYNTHESIZE,
    code_agent,
    knowledge_agent,
    planner_agent,
    research_agent,
    synthesizer_agent,
)
from app.services.agents.state import AgentState

logger = logging.getLogger(__name__)

RouteName = Literal["knowledge_agent", "code_agent", "research_agent", "synthesizer_agent"]


def build_agent_graph() -> CompiledStateGraph:
    """
    Build and compile the DevTwin shared-state multi-agent graph.

    Data-gathering nodes return to the planner so routing can inspect the
    updated state before deciding whether another specialist is needed.
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("planner_agent", planner_agent)
    workflow.add_node("knowledge_agent", knowledge_agent)
    workflow.add_node("code_agent", code_agent)
    workflow.add_node("research_agent", research_agent)
    workflow.add_node("synthesizer_agent", synthesizer_agent)

    workflow.set_entry_point("planner_agent")

    workflow.add_conditional_edges(
        "planner_agent",
        route_from_planner,
        {
            "knowledge_agent": "knowledge_agent",
            "code_agent": "code_agent",
            "research_agent": "research_agent",
            "synthesizer_agent": "synthesizer_agent",
        },
    )
    workflow.add_edge("knowledge_agent", "planner_agent")
    workflow.add_edge("code_agent", "planner_agent")
    workflow.add_edge("research_agent", "planner_agent")
    workflow.add_edge("synthesizer_agent", END)

    compiled = workflow.compile()
    logger.info("DevTwin LangGraph agent workflow compiled.")
    return compiled


def route_from_planner(state: AgentState) -> RouteName:
    """Map AgentState.next_step to a concrete LangGraph node name."""
    next_step = state.get("next_step", NEXT_SYNTHESIZE)

    if next_step == NEXT_KNOWLEDGE:
        return "knowledge_agent"
    if next_step == NEXT_CODE:
        return "code_agent"
    if next_step == NEXT_RESEARCH:
        return "research_agent"

    return "synthesizer_agent"


agent_graph = build_agent_graph()
