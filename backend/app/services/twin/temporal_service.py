"""
app/services/twin/temporal_service.py

Temporal skill evolution over the Neo4j Personal Learning Graph.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from app.services.graph.graph_service import GraphConfigurationError, GraphService, GraphServiceError
from app.services.twin.skill_service import DEFAULT_USER_ID

logger = logging.getLogger(__name__)

REPOSITORY_SKILL_WEIGHT = 30
PAPER_SKILL_WEIGHT = 15
MAX_SKILL_SCORE = 100
DEFAULT_TOP_SKILLS = 6


@dataclass(frozen=True, slots=True)
class SkillEvidenceEvent:
    """One dated skill evidence event from a repository or paper."""

    skill_name: str
    year: int
    contribution: int
    source_key: str


class TemporalSkillService:
    """Service for calculating cumulative skill evolution timelines."""

    def __init__(self, graph_service: GraphService, user_id: str = DEFAULT_USER_ID) -> None:
        self._graph_service = graph_service
        self._user_id = user_id

    async def get_skill_evolution(self, top_n: int = DEFAULT_TOP_SKILLS) -> list[dict[str, str | int]]:
        """
        Return a Recharts-ready cumulative skill timeline.

        Each row is shaped like:
            {"date": "2025", "FastAPI": 60, "Next.js": 30}
        """
        if top_n <= 0:
            raise ValueError("top_n must be greater than 0.")
        if top_n > 12:
            raise ValueError("top_n must be less than or equal to 12.")

        try:
            async with self._graph_service.driver.session() as session:
                result = await session.run(
                    _SKILL_EVOLUTION_EVENTS_QUERY,
                    user_id=self._user_id,
                    repository_weight=REPOSITORY_SKILL_WEIGHT,
                    paper_weight=PAPER_SKILL_WEIGHT,
                )
                rows = await result.data()
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Temporal skill evolution query failed: %s", exc)
            raise GraphServiceError("Failed to query temporal skill evolution.") from exc

        events = _rows_to_events(rows)
        timeline = _build_cumulative_timeline(events, top_n=top_n)
        logger.info(
            "Calculated temporal skill evolution for user_id=%s years=%d skills=%d.",
            self._user_id,
            len(timeline),
            max((len(row) - 1 for row in timeline), default=0),
        )
        return timeline


def _rows_to_events(rows: list[dict[str, Any]]) -> list[SkillEvidenceEvent]:
    """Normalize Neo4j rows into dated skill evidence events."""
    events: list[SkillEvidenceEvent] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        skill_name = _clean_skill_name(row.get("skill_name"))
        if skill_name is None:
            continue

        source_key = _source_key(row)
        dedupe_key = (skill_name.lower(), source_key)
        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        events.append(
            SkillEvidenceEvent(
                skill_name=skill_name,
                year=_extract_year(row.get("raw_date")),
                contribution=int(row.get("contribution") or 0),
                source_key=source_key,
            )
        )

    return events


def _build_cumulative_timeline(
    events: list[SkillEvidenceEvent],
    top_n: int,
) -> list[dict[str, str | int]]:
    """Build cumulative yearly scores for the top skills."""
    if not events:
        return []

    total_by_skill: dict[str, int] = defaultdict(int)
    for event in events:
        total_by_skill[event.skill_name] += event.contribution

    top_skills = [
        skill
        for skill, _score in sorted(
            total_by_skill.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )[:top_n]
    ]
    top_skill_set = set(top_skills)

    events_by_year: dict[int, list[SkillEvidenceEvent]] = defaultdict(list)
    for event in events:
        if event.skill_name in top_skill_set:
            events_by_year[event.year].append(event)

    cumulative = {skill: 0 for skill in top_skills}
    timeline: list[dict[str, str | int]] = []

    for year in sorted(events_by_year):
        for event in events_by_year[year]:
            cumulative[event.skill_name] = min(
                MAX_SKILL_SCORE,
                cumulative[event.skill_name] + event.contribution,
            )

        row: dict[str, str | int] = {"date": str(year)}
        for skill in top_skills:
            row[skill] = cumulative[skill]
        timeline.append(row)

    return timeline


def _clean_skill_name(raw_name: Any) -> str | None:
    """Normalize graph skill node names and discard noisy parser note nodes."""
    if not isinstance(raw_name, str):
        return None

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


def _source_key(row: dict[str, Any]) -> str:
    """Build a stable source key for event deduplication."""
    return "|".join(
        [
            str(row.get("source_type") or "unknown"),
            str(row.get("source_id") or row.get("source_name") or "unknown"),
        ]
    )


def _extract_year(raw_value: Any) -> int:
    """Extract a year from Neo4j temporal values, datetimes, dates, or strings."""
    if raw_value is None:
        return datetime.now(UTC).year

    year = getattr(raw_value, "year", None)
    if isinstance(year, int):
        return year

    if isinstance(raw_value, datetime | date):
        return raw_value.year

    text = str(raw_value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])

    return datetime.now(UTC).year


_SKILL_EVOLUTION_EVENTS_QUERY = """
MATCH (user:User {id: $user_id})
CALL (user) {
  MATCH (user)-[:OWNER_OF]->(source:Repository)-[:USES]->(skill)
  WHERE skill:Technology OR skill:Framework
  WITH source, skill, properties(source) AS source_props
  RETURN
    skill.name AS skill_name,
    coalesce(
      source_props.created_at,
      source_props.createdAt,
      source_props.updated_at,
      source_props.updatedAt,
      source_props.pushed_at,
      source_props.pushedAt,
      source_props.ingested_at,
      source_props.ingestedAt
    ) AS raw_date,
    "repository" AS source_type,
    source.id AS source_id,
    coalesce(source.repo_name, source.github_url, toString(source.id)) AS source_name,
    $repository_weight AS contribution

  UNION ALL

  MATCH (user)-[:READ]->(source:Paper)-[:MENTIONS_TECH]->(skill)
  WHERE skill:Technology OR skill:Framework
  WITH source, skill, properties(source) AS source_props
  RETURN
    skill.name AS skill_name,
    coalesce(
      source_props.uploaded_at,
      source_props.uploadedAt,
      source_props.upload_timestamp,
      source_props.uploadTimestamp,
      source_props.created_at,
      source_props.createdAt,
      source_props.ingested_at,
      source_props.ingestedAt
    ) AS raw_date,
    "paper" AS source_type,
    source.id AS source_id,
    coalesce(source.title, source.filename, toString(source.id)) AS source_name,
    $paper_weight AS contribution
}
RETURN skill_name, raw_date, source_type, source_id, source_name, contribution
ORDER BY raw_date ASC, skill_name ASC
"""
