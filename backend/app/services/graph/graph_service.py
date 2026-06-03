"""
app/services/graph/graph_service.py

Neo4j connection and schema-constraint management for Phase 3.

This service owns the async Neo4j driver lifecycle and creates uniqueness
constraints for the core graph node labels before graph writes are introduced.
"""

import logging
from collections.abc import Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase
from neo4j.graph import Node, Relationship
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class GraphConfigurationError(RuntimeError):
    """Raised when Neo4j settings are missing or incomplete."""


class GraphServiceError(RuntimeError):
    """Raised when Neo4j connection or schema initialization fails."""


class GraphService:
    """Async Neo4j AuraDB connection service."""

    _CONSTRAINT_QUERIES: Sequence[str] = (
        """
        CREATE CONSTRAINT user_id_unique IF NOT EXISTS
        FOR (node:User)
        REQUIRE node.id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT repository_id_unique IF NOT EXISTS
        FOR (node:Repository)
        REQUIRE node.id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT paper_id_unique IF NOT EXISTS
        FOR (node:Paper)
        REQUIRE node.id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT concept_name_unique IF NOT EXISTS
        FOR (node:Concept)
        REQUIRE node.name IS UNIQUE
        """,
        """
        CREATE CONSTRAINT technology_name_unique IF NOT EXISTS
        FOR (node:Technology)
        REQUIRE node.name IS UNIQUE
        """,
        """
        CREATE CONSTRAINT framework_name_unique IF NOT EXISTS
        FOR (node:Framework)
        REQUIRE node.name IS UNIQUE
        """,
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._driver: AsyncDriver | None = None

    @property
    def is_configured(self) -> bool:
        """Return True when all required Neo4j credentials are present."""
        return bool(
            self._settings.neo4j_uri
            and self._settings.neo4j_user
            and self._settings.neo4j_password
        )

    @property
    def driver(self) -> AsyncDriver:
        """Return the initialized async Neo4j driver, creating it lazily."""
        if self._driver is None:
            self._driver = self._create_driver()
        return self._driver

    async def close(self) -> None:
        """Gracefully close the Neo4j driver connection pool."""
        if self._driver is None:
            return

        await self._driver.close()
        self._driver = None
        logger.info("Neo4j driver connection closed.")

    async def verify_connection(self) -> None:
        """Run a minimal Cypher query to verify the Neo4j connection."""
        try:
            async with self.driver.session() as session:
                result = await session.run("RETURN 1 AS num")
                record = await result.single(strict=True)

            if record["num"] != 1:
                raise GraphServiceError("Neo4j health check returned an unexpected result.")

            logger.info("Neo4j connection verified successfully.")
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Neo4j connection verification failed: %s", exc)
            raise GraphServiceError("Failed to verify Neo4j connection.") from exc

    async def initialize_constraints(self) -> None:
        """Create idempotent uniqueness constraints for core graph node labels."""
        try:
            async with self.driver.session() as session:
                for query in self._CONSTRAINT_QUERIES:
                    result = await session.run(query)
                    await result.consume()

            logger.info(
                "Neo4j uniqueness constraints verified for User, Repository, Paper, "
                "Concept, Technology, and Framework."
            )
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Neo4j constraint initialization failed: %s", exc)
            raise GraphServiceError("Failed to initialize Neo4j constraints.") from exc

    async def get_subgraph(
        self,
        node_name: str | None = None,
        limit: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Return a React Flow-friendly subgraph from Neo4j.

        When node_name is provided, the query finds a matching node by name,
        title, repo_name, or id and returns relationships within 1-2 hops.
        Without node_name, it returns a broad graph overview.
        """
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")
        if limit > 250:
            raise ValueError("limit must be less than or equal to 250.")

        normalized_node_name = node_name.strip() if node_name else None
        if node_name is not None and not normalized_node_name:
            raise ValueError("node_name cannot be blank.")

        query = _NODE_SUBGRAPH_QUERY if normalized_node_name else _OVERVIEW_SUBGRAPH_QUERY
        params: dict[str, Any] = {"limit": limit}
        if normalized_node_name:
            params["node_name"] = normalized_node_name
            params["node_name_lower"] = normalized_node_name.lower()

        try:
            async with self.driver.session() as session:
                result = await session.run(query, **params)
                rows = [
                    {"n": record["n"], "r": record["r"], "m": record["m"]}
                    async for record in result
                ]
        except (GraphConfigurationError, AuthError, ServiceUnavailable, Neo4jError) as exc:
            logger.exception("Neo4j subgraph query failed: %s", exc)
            raise GraphServiceError("Failed to query Neo4j subgraph.") from exc

        subgraph = _format_subgraph_rows(rows)
        logger.info(
            "Neo4j subgraph query returned %d nodes and %d edges.",
            len(subgraph["nodes"]),
            len(subgraph["edges"]),
        )
        return subgraph

    def _create_driver(self) -> AsyncDriver:
        """Create the async Neo4j driver from application settings."""
        if not self.is_configured:
            raise GraphConfigurationError(
                "Neo4j is not configured. Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD."
            )

        assert self._settings.neo4j_uri is not None
        assert self._settings.neo4j_user is not None
        assert self._settings.neo4j_password is not None

        logger.info("Initializing Neo4j async driver.")
        return AsyncGraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            connection_timeout=15,
        )


_OVERVIEW_SUBGRAPH_QUERY = """
MATCH (n)-[r]->(m)
RETURN n, r, m
LIMIT $limit
"""

_NODE_SUBGRAPH_QUERY = """
MATCH (start)
WHERE toLower(coalesce(start.name, "")) = $node_name_lower
   OR toLower(coalesce(start.title, "")) = $node_name_lower
   OR toLower(coalesce(start.repo_name, "")) = $node_name_lower
   OR toString(start.id) = $node_name
WITH start
LIMIT 1
MATCH path=(start)-[*1..2]-(connected)
WITH path
LIMIT $limit
UNWIND relationships(path) AS r
WITH DISTINCT r
RETURN startNode(r) AS n, r, endNode(r) AS m
LIMIT $limit
"""

_PREFERRED_LABEL_ORDER = (
    "Repository",
    "Paper",
    "Concept",
    "Technology",
    "Framework",
)


def _format_subgraph_rows(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Format Neo4j n/r/m rows into unique node and edge dictionaries."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    for row in rows:
        source = row.get("n")
        relationship = row.get("r")
        target = row.get("m")

        if not isinstance(source, Node) or not isinstance(target, Node):
            continue

        source_payload = _format_node(source)
        target_payload = _format_node(target)
        nodes[source_payload["id"]] = source_payload
        nodes[target_payload["id"]] = target_payload

        if isinstance(relationship, Relationship):
            edge_payload = _format_edge(relationship, source, target)
            edges[edge_payload["id"]] = edge_payload

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


def _format_node(node: Node) -> dict[str, Any]:
    """Convert a Neo4j node to the public GraphNode shape."""
    label = _primary_label(node)
    properties = _json_safe_properties(dict(node))
    return {
        "id": _node_id(node),
        "label": label,
        "properties": properties,
    }


def _format_edge(
    relationship: Relationship,
    source: Node,
    target: Node,
) -> dict[str, Any]:
    """Convert a Neo4j relationship to the public GraphEdge shape."""
    source_id = _node_id(source)
    target_id = _node_id(target)
    edge_type = relationship.type
    edge_id = f"{source_id}:{edge_type}:{target_id}"

    return {
        "id": edge_id,
        "source": source_id,
        "target": target_id,
        "type": edge_type,
        "properties": _json_safe_properties(dict(relationship)),
    }


def _primary_label(node: Node) -> str:
    """Choose one stable display label from a Neo4j node's labels."""
    labels = set(node.labels)
    for label in _PREFERRED_LABEL_ORDER:
        if label in labels:
            return label
    return sorted(labels)[0] if labels else "Node"


def _node_id(node: Node) -> str:
    """Build a stable frontend ID from graph labels and unique properties."""
    label = _primary_label(node)
    properties = dict(node)

    if "id" in properties and properties["id"] is not None:
        return f"{label}:{properties['id']}"
    if "name" in properties and properties["name"] is not None:
        return f"{label}:{properties['name']}"
    if "title" in properties and properties["title"] is not None:
        return f"{label}:{properties['title']}"

    return f"{label}:{node.element_id}"


def _json_safe_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Return properties that Pydantic/FastAPI can serialize predictably."""
    safe: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, str | int | float | bool) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [
                item if isinstance(item, str | int | float | bool) or item is None else str(item)
                for item in value
            ]
        elif isinstance(value, dict):
            safe[key] = {
                str(nested_key): (
                    nested_value
                    if isinstance(nested_value, str | int | float | bool) or nested_value is None
                    else str(nested_value)
                )
                for nested_key, nested_value in value.items()
            }
        else:
            safe[key] = str(value)

    return safe
