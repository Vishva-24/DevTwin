"""
app/services/twin/skill_service.py

Skill inference over the Neo4j Personal Learning Graph.
"""

import logging
from typing import Any

from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from app.services.graph.graph_service import GraphConfigurationError, GraphService, GraphServiceError

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "me"

TARGET_ROLE_SKILLS: dict[str, set[str]] = {
    "mlengineer": {
        "python",
        "pytorch",
        "tensorflow",
        "scikit-learn",
        "pandas",
        "numpy",
        "fastapi",
        "docker",
        "postgresql",
        "qdrant",
        "neo4j",
        "vector search",
        "transformers",
    },
    "backendengineer": {
        "python",
        "fastapi",
        "postgresql",
        "sqlalchemy",
        "docker",
        "redis",
        "neo4j",
        "github",
    },
    "aiengineer": {
        "python",
        "gemini",
        "fastapi",
        "qdrant",
        "neo4j",
        "vector search",
        "rag",
        "transformers",
        "pytorch",
        "sentence-transformers",
    },
}


class SkillService:
    """Service for personal skill inference and gap analysis."""

    def __init__(self, graph_service: GraphService, user_id: str = DEFAULT_USER_ID) -> None:
        self._graph_service = graph_service
        self._user_id = user_id

    async def calculate_skill_strengths(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Infer strengths from owned repositories and read papers.

        Heuristic:
        - each owned repository using a skill contributes 30 points
        - each read paper mentioning a skill contributes 15 points
        - score is capped at 100 and normalized into HAS_SKILL.confidence
        """
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")
        if limit > 250:
            raise ValueError("limit must be less than or equal to 250.")

        try:
            async with self._graph_service.driver.session() as session:
                result = await session.run(
                    _CALCULATE_SKILLS_QUERY,
                    user_id=self._user_id,
                    limit=limit,
                )
                rows = await result.data()
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Skill strength calculation failed: %s", exc)
            raise GraphServiceError("Failed to calculate skill strengths.") from exc

        skills = [_format_skill_strength(row) for row in rows]
        logger.info("Calculated %d skill strengths for user_id=%s.", len(skills), self._user_id)
        return skills

    async def get_profile(self) -> dict[str, Any]:
        """Return basic User node statistics."""
        try:
            async with self._graph_service.driver.session() as session:
                result = await session.run(_PROFILE_QUERY, user_id=self._user_id)
                row = await result.single()
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Twin profile query failed: %s", exc)
            raise GraphServiceError("Failed to query twin profile.") from exc

        if row is None:
            return {
                "id": self._user_id,
                "name": "Developer",
                "repository_count": 0,
                "paper_count": 0,
                "skill_count": 0,
                "average_confidence": 0.0,
            }

        return {
            "id": str(row["id"]),
            "name": str(row["name"] or "Developer"),
            "repository_count": int(row["repository_count"] or 0),
            "paper_count": int(row["paper_count"] or 0),
            "skill_count": int(row["skill_count"] or 0),
            "average_confidence": float(row["average_confidence"] or 0.0),
        }

    async def get_skill_gap(self, target_role: str) -> dict[str, Any]:
        """
        Return current skills and graph-mentioned missing skills for a target role.

        Missing skills are Technology/Framework nodes mentioned by papers or
        otherwise present in the graph that do not currently have a HAS_SKILL
        edge from the User.
        """
        normalized_role = _normalize_role(target_role)
        role_skills = sorted(TARGET_ROLE_SKILLS.get(normalized_role, set()))
        use_role_filter = bool(role_skills)

        try:
            async with self._graph_service.driver.session() as session:
                result = await session.run(
                    _SKILL_GAP_QUERY,
                    user_id=self._user_id,
                    role_skills=role_skills,
                    use_role_filter=use_role_filter,
                )
                missing_rows = await result.data()

                have_result = await session.run(
                    _CURRENT_SKILLS_QUERY,
                    user_id=self._user_id,
                    role_skills=role_skills,
                    use_role_filter=use_role_filter,
                    limit=20,
                )
                have_rows = await have_result.data()
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Skill gap query failed: %s", exc)
            raise GraphServiceError("Failed to query skill gap.") from exc

        return {
            "target_role": target_role,
            "have": [_format_skill_strength(row) for row in have_rows],
            "missing": [_format_gap_item(row) for row in missing_rows],
        }


def _format_skill_strength(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Neo4j row into the SkillStrength schema shape."""
    return {
        "name": str(row["name"]),
        "label": str(row["label"]),
        "confidence": float(row["confidence"] or 0.0),
        "strength_score": float(row["strength_score"] or 0.0),
        "project_count": int(row["project_count"] or 0),
        "paper_count": int(row["paper_count"] or 0),
    }


def _format_gap_item(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Neo4j row into the SkillGapItem schema shape."""
    return {
        "name": str(row["name"]),
        "label": str(row["label"]),
        "paper_count": int(row["paper_count"] or 0),
        "graph_count": int(row["graph_count"] or 0),
        "relevance_score": float(row["relevance_score"] or 0.0),
    }


def _normalize_role(target_role: str) -> str:
    """Normalize a target role string for lookup."""
    return "".join(character for character in target_role.lower() if character.isalnum())


_PROFILE_QUERY = """
MATCH (u:User {id: $user_id})
OPTIONAL MATCH (u)-[:OWNER_OF]->(repo:Repository)
WITH u, count(DISTINCT repo) AS repository_count
OPTIONAL MATCH (u)-[:READ]->(paper:Paper)
WITH u, repository_count, count(DISTINCT paper) AS paper_count
OPTIONAL MATCH (u)-[skill_rel:HAS_SKILL]->(skill)
WHERE skill:Technology OR skill:Framework
RETURN
  u.id AS id,
  u.name AS name,
  repository_count,
  paper_count,
  count(DISTINCT skill) AS skill_count,
  coalesce(avg(skill_rel.confidence), 0.0) AS average_confidence
"""

_CALCULATE_SKILLS_QUERY = """
MATCH (u:User {id: $user_id})
CALL (u) {
  MATCH (u)-[:OWNER_OF]->(repo:Repository)-[:USES]->(skill)
  WHERE skill:Technology OR skill:Framework
  RETURN skill, repo.id AS project_id, null AS paper_id
  UNION
  MATCH (u)-[:READ]->(paper:Paper)-[:MENTIONS_TECH]->(skill)
  WHERE skill:Technology OR skill:Framework
  RETURN skill, null AS project_id, paper.id AS paper_id
}
WITH
  u,
  skill,
  collect(DISTINCT project_id) AS project_ids,
  collect(DISTINCT paper_id) AS paper_ids
WHERE skill IS NOT NULL
WITH
  u,
  skill,
  [id IN project_ids WHERE id IS NOT NULL] AS project_ids,
  [id IN paper_ids WHERE id IS NOT NULL] AS paper_ids
WITH
  u,
  skill,
  size(project_ids) AS project_count,
  size(paper_ids) AS paper_count
WITH
  u,
  skill,
  project_count,
  paper_count,
  CASE
    WHEN (project_count * 30 + paper_count * 15) > 100 THEN 100
    ELSE (project_count * 30 + paper_count * 15)
  END AS strength_score
MERGE (u)-[skill_rel:HAS_SKILL]->(skill)
SET
  skill_rel.confidence = toFloat(strength_score) / 100.0,
  skill_rel.strength_score = toFloat(strength_score),
  skill_rel.project_count = project_count,
  skill_rel.paper_count = paper_count,
  skill_rel.updated_at = datetime()
RETURN
  skill.name AS name,
  CASE WHEN skill:Framework THEN "Framework" ELSE "Technology" END AS label,
  skill_rel.confidence AS confidence,
  skill_rel.strength_score AS strength_score,
  project_count,
  paper_count
ORDER BY strength_score DESC, project_count DESC, paper_count DESC, name ASC
LIMIT $limit
"""

_CURRENT_SKILLS_QUERY = """
MATCH (u:User {id: $user_id})-[skill_rel:HAS_SKILL]->(skill)
WHERE skill:Technology OR skill:Framework
WITH u, skill, skill_rel
WHERE NOT $use_role_filter OR toLower(skill.name) IN $role_skills
RETURN
  skill.name AS name,
  CASE WHEN skill:Framework THEN "Framework" ELSE "Technology" END AS label,
  skill_rel.confidence AS confidence,
  skill_rel.strength_score AS strength_score,
  skill_rel.project_count AS project_count,
  skill_rel.paper_count AS paper_count
ORDER BY strength_score DESC, project_count DESC, paper_count DESC, name ASC
LIMIT $limit
"""

_SKILL_GAP_QUERY = """
MATCH (u:User {id: $user_id})
MATCH (candidate)
WHERE candidate:Technology OR candidate:Framework
WITH u, candidate
WHERE NOT $use_role_filter OR toLower(candidate.name) IN $role_skills
OPTIONAL MATCH (u)-[:READ]->(paper:Paper)-[:MENTIONS_TECH]->(candidate)
WITH u, candidate, count(DISTINCT paper) AS paper_count
OPTIONAL MATCH (repo:Repository)-[:USES]->(candidate)
WITH u, candidate, paper_count, count(DISTINCT repo) AS repository_count
WHERE (paper_count > 0 OR repository_count > 0 OR $use_role_filter)
  AND NOT EXISTS { MATCH (u)-[:HAS_SKILL]->(candidate) }
WITH
  candidate,
  paper_count,
  (paper_count + repository_count) AS graph_count,
  (paper_count * 10 + repository_count * 3) AS relevance_score
RETURN
  candidate.name AS name,
  CASE WHEN candidate:Framework THEN "Framework" ELSE "Technology" END AS label,
  paper_count,
  graph_count,
  relevance_score
ORDER BY relevance_score DESC, paper_count DESC, name ASC
LIMIT 25
"""
