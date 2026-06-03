"""
app/schemas/graph.py

Pydantic response schemas for Neo4j graph exploration endpoints.

The shape is intentionally React Flow friendly: nodes and edges are flat lists
with stable string IDs and property dictionaries.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphNode(BaseModel):
    """A graph node formatted for frontend graph visualization."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        description="Stable frontend node identifier, e.g. Technology:FastAPI.",
    )
    label: str = Field(
        ...,
        description="Primary Neo4j label, e.g. Repository, Paper, Technology.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Serializable Neo4j node properties such as name, title, or github_url.",
    )


class GraphEdge(BaseModel):
    """A directed graph edge formatted for frontend graph visualization."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        description="Stable frontend edge identifier.",
    )
    source: str = Field(
        ...,
        description="Source GraphNode.id.",
    )
    target: str = Field(
        ...,
        description="Target GraphNode.id.",
    )
    type: str = Field(
        ...,
        description="Neo4j relationship type, e.g. USES or EXPLAINS_CONCEPT.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Serializable Neo4j relationship properties.",
    )


class GraphResponse(BaseModel):
    """Structured graph exploration response."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="Unique graph nodes included in this subgraph.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="Unique graph edges included in this subgraph.",
    )
