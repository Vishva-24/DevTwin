"""
app/api/v1/twin.py

FastAPI endpoints for Phase 4 Personal Learning Graph.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.twin import (
    SkillEvolutionPoint,
    SkillGapResponse,
    SkillStrength,
    TwinProfileResponse,
)
from app.services.graph.graph_service import GraphService, GraphServiceError
from app.services.twin.skill_service import SkillService
from app.services.twin.temporal_service import TemporalSkillService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/twin", tags=["Personal Twin"])


def _get_graph_service(request: Request) -> GraphService:
    """Return the lifespan-managed graph service or raise a 503."""
    graph_service = getattr(request.app.state, "graph_service", None)
    graph_ready = bool(getattr(request.app.state, "graph_ready", False))

    if not graph_ready or graph_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j graph service is not ready.",
        )

    return graph_service


@router.get(
    "/profile",
    response_model=TwinProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get personal twin profile",
)
async def get_twin_profile(request: Request) -> TwinProfileResponse:
    """Return the User node and basic Personal Learning Graph stats."""
    try:
        service = SkillService(_get_graph_service(request))
        return TwinProfileResponse.model_validate(await service.get_profile())
    except GraphServiceError as exc:
        logger.exception("Twin profile failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query the personal twin profile.",
        ) from exc


@router.get(
    "/skills",
    response_model=list[SkillStrength],
    status_code=status.HTTP_200_OK,
    summary="Get inferred personal skills",
)
async def get_twin_skills(
    request: Request,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum number of skills to return."),
    ] = 50,
) -> list[SkillStrength]:
    """Recalculate and return top skills sorted by strength score."""
    try:
        service = SkillService(_get_graph_service(request))
        skills = await service.calculate_skill_strengths(limit=limit)
        return [SkillStrength.model_validate(skill) for skill in skills]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except GraphServiceError as exc:
        logger.exception("Twin skills failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not infer personal skills.",
        ) from exc


@router.get(
    "/skill-gap",
    response_model=SkillGapResponse,
    status_code=status.HTTP_200_OK,
    summary="Get skill gap for a target role",
)
async def get_twin_skill_gap(
    request: Request,
    target: Annotated[
        str,
        Query(min_length=1, max_length=80, description="Target role, e.g. MLEngineer."),
    ] = "MLEngineer",
) -> SkillGapResponse:
    """Return current and missing skills for a target role."""
    try:
        service = SkillService(_get_graph_service(request))
        return SkillGapResponse.model_validate(await service.get_skill_gap(target_role=target))
    except GraphServiceError as exc:
        logger.exception("Twin skill gap failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not calculate skill gap.",
        ) from exc


@router.get(
    "/evolution",
    response_model=list[SkillEvolutionPoint],
    status_code=status.HTTP_200_OK,
    summary="Get temporal skill evolution",
)
async def get_twin_skill_evolution(request: Request) -> list[SkillEvolutionPoint]:
    """Return cumulative yearly skill mastery scores for Recharts."""
    try:
        service = TemporalSkillService(_get_graph_service(request))
        return await service.get_skill_evolution()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except GraphServiceError as exc:
        logger.exception("Twin skill evolution failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not calculate temporal skill evolution.",
        ) from exc
