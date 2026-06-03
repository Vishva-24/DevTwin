"""
app/api/v1/graph.py

FastAPI endpoints for exploring the Neo4j knowledge graph.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.graph import GraphResponse
from app.services.graph.graph_service import GraphService, GraphServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/graph", tags=["Knowledge Graph"])


def _get_graph_service(request: Request) -> GraphService:
    """Return the lifespan-managed graph service or raise a 503."""
    graph_service = getattr(request.app.state, "graph_service", None)
    graph_ready = bool(getattr(request.app.state, "graph_ready", False))

    if not graph_ready or graph_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Neo4j graph service is not ready. Check NEO4J_URI, NEO4J_USER, "
                "NEO4J_PASSWORD, and backend startup logs."
            ),
        )

    return graph_service


@router.get(
    "/explore",
    response_model=GraphResponse,
    status_code=status.HTTP_200_OK,
    summary="Explore the Neo4j knowledge graph",
)
async def explore_graph(
    request: Request,
    node_name: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=255,
            description=(
                "Optional node lookup value. Matches name, title, repo_name, or id. "
                "Example: FastAPI."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=250,
            description="Maximum number of relationships to return.",
        ),
    ] = 50,
) -> GraphResponse:
    """
    Return a structured graph overview or a 1-2 hop subgraph around a node.

    The response is intentionally shaped for React Flow: flat node and edge
    arrays with stable IDs, labels, relationship types, and property payloads.
    """
    graph_service = _get_graph_service(request)

    try:
        subgraph = await graph_service.get_subgraph(node_name=node_name, limit=limit)
        return GraphResponse.model_validate(subgraph)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except GraphServiceError as exc:
        logger.exception("Graph exploration failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Neo4j graph query failed.",
        ) from exc
