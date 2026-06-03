"""
scripts/ingest_repo_graph.py

Standalone migration script for Phase 3:
ingest Phase 1 repository intelligence from PostgreSQL into Neo4j.

Run from the backend directory:
    .\\.venv\\Scripts\\python.exe scripts\\ingest_repo_graph.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from neo4j import AsyncManagedTransaction  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.repository import Analysis, AnalysisStatus, Repository  # noqa: E402
from app.services.graph.graph_service import GraphService  # noqa: E402

logger = logging.getLogger("ingest_repo_graph")

NodeLabel = Literal["Technology", "Framework"]

FRAMEWORK_KEYS = {
    "framework",
    "frameworks",
    "frontend",
    "backend",
    "web_frameworks",
    "ui_libraries",
    "component_libraries",
}
TECHNOLOGY_KEYS = {
    "language",
    "languages",
    "database",
    "databases",
    "infrastructure",
    "infra",
    "devops",
    "deployment",
    "package_manager",
    "package_managers",
    "runtime",
    "runtimes",
    "tool",
    "tools",
    "testing",
    "orm",
    "apis",
}
IGNORED_KEYS = {
    "confidence",
    "evidence",
    "source",
    "sources",
    "file",
    "files",
    "path",
    "paths",
    "line",
    "lines",
    "reason",
    "reasons",
    "detected",
    "score",
    "scores",
    "count",
    "counts",
}
NAME_KEYS = (
    "name",
    "technology",
    "framework",
    "language",
    "database",
    "tool",
    "package",
    "package_manager",
    "runtime",
)


@dataclass(frozen=True, slots=True)
class RepositoryStackRecord:
    """Repository plus the latest completed tech_stack JSONB payload."""

    repository_id: UUID
    repo_name: str
    github_url: str
    tech_stack: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class TechStackItem:
    """One graph-worthy technology/framework extracted from tech_stack JSON."""

    name: str
    label: NodeLabel
    category: str


@dataclass(frozen=True, slots=True)
class IngestionStats:
    """High-level migration counts."""

    repositories_seen: int = 0
    repositories_ingested: int = 0
    repositories_without_stack: int = 0
    unique_stack_items: int = 0
    uses_relationships: int = 0


def configure_logging() -> None:
    """Configure script logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


async def fetch_repository_stack_records(db: AsyncSession) -> list[RepositoryStackRecord]:
    """
    Fetch repositories and their latest completed analysis tech_stack payload.

    The current DevTwin schema stores parser output in separate JSONB columns,
    so this script reads Analysis.tech_stack rather than a monolithic
    analysis_json column.
    """
    stmt = (
        select(Repository, Analysis)
        .outerjoin(Analysis, Repository.id == Analysis.repository_id)
        .order_by(Repository.id, Analysis.created_at.desc().nullslast())
    )
    rows = (await db.execute(stmt)).all()

    records: list[RepositoryStackRecord] = []
    seen_repository_ids: set[UUID] = set()

    for repository, analysis in rows:
        if repository.id in seen_repository_ids:
            continue

        seen_repository_ids.add(repository.id)
        selected_analysis = analysis if _analysis_has_usable_stack(analysis) else None

        if selected_analysis is None:
            selected_analysis = _latest_usable_analysis_for_repository(repository.id, rows)

        records.append(
            RepositoryStackRecord(
                repository_id=repository.id,
                repo_name=repository.repo_name,
                github_url=repository.github_url,
                tech_stack=(
                    selected_analysis.tech_stack
                    if selected_analysis is not None
                    and isinstance(selected_analysis.tech_stack, Mapping)
                    else None
                ),
            )
        )

    return records


async def ingest_repository_graph(
    graph_service: GraphService,
    records: Sequence[RepositoryStackRecord],
) -> IngestionStats:
    """Write repository, technology/framework nodes, and USES relationships to Neo4j."""
    repositories_ingested = 0
    repositories_without_stack = 0
    unique_stack_items_total = 0
    relationships_total = 0

    async with graph_service.driver.session() as session:
        for record in records:
            items = extract_tech_stack_items(record.tech_stack)
            if not items:
                repositories_without_stack += 1
                logger.warning(
                    "Skipping stack relationships for repository %s (%s): no tech_stack items.",
                    record.repo_name,
                    record.repository_id,
                )

            result = await session.execute_write(_merge_repository_and_stack, record, items)
            repositories_ingested += 1
            unique_stack_items_total += result["unique_stack_items"]
            relationships_total += result["uses_relationships"]

            logger.info(
                "Ingested repo=%s tech_items=%d uses_edges=%d",
                record.repo_name,
                result["unique_stack_items"],
                result["uses_relationships"],
            )

    return IngestionStats(
        repositories_seen=len(records),
        repositories_ingested=repositories_ingested,
        repositories_without_stack=repositories_without_stack,
        unique_stack_items=unique_stack_items_total,
        uses_relationships=relationships_total,
    )


def extract_tech_stack_items(tech_stack: Mapping[str, Any] | None) -> list[TechStackItem]:
    """Extract unique Technology/Framework graph candidates from parser JSON."""
    if not tech_stack:
        return []

    found: dict[tuple[NodeLabel, str], TechStackItem] = {}

    for item in _walk_stack_values(tech_stack):
        normalized = normalize_stack_name(item.name)
        if normalized is None:
            continue

        key = (item.label, normalized.lower())
        found[key] = TechStackItem(
            name=normalized,
            label=item.label,
            category=item.category,
        )

    return sorted(found.values(), key=lambda value: (value.label, value.name.lower()))


def normalize_stack_name(value: Any) -> str | None:
    """Normalize a stack item into a graph node name."""
    if not isinstance(value, str):
        return None

    normalized = re.sub(r"\\s+", " ", value).strip()
    normalized = normalized.strip("`'\"[]{}()")
    if len(normalized) < 2:
        return None

    if normalized.lower() in {"none", "unknown", "n/a", "not detected", "null"}:
        return None

    return normalized


def _walk_stack_values(
    value: Any,
    key_path: tuple[str, ...] = (),
) -> Iterable[TechStackItem]:
    """Recursively walk flexible tech_stack JSON shapes."""
    if isinstance(value, Mapping):
        explicit_name = _extract_explicit_name(value)
        if explicit_name is not None:
            yield TechStackItem(
                name=explicit_name,
                label=_label_for_path(key_path),
                category=_category_for_path(key_path),
            )

        for raw_key, nested_value in value.items():
            key = str(raw_key).lower()
            if key in IGNORED_KEYS or key in NAME_KEYS:
                continue
            yield from _walk_stack_values(nested_value, key_path + (key,))
        return

    if isinstance(value, list | tuple | set):
        for nested_value in value:
            yield from _walk_stack_values(nested_value, key_path)
        return

    if isinstance(value, str):
        yield TechStackItem(
            name=value,
            label=_label_for_path(key_path),
            category=_category_for_path(key_path),
        )


def _extract_explicit_name(value: Mapping[str, Any]) -> str | None:
    """Extract a name from a dict-shaped stack item."""
    for key in NAME_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    return None


def _label_for_path(key_path: tuple[str, ...]) -> NodeLabel:
    """Choose Framework or Technology based on the surrounding JSON key path."""
    lowered = set(key_path)
    if lowered & FRAMEWORK_KEYS:
        return "Framework"
    if lowered & TECHNOLOGY_KEYS:
        return "Technology"
    return "Technology"


def _category_for_path(key_path: tuple[str, ...]) -> str:
    """Use the nearest meaningful JSON key as the relationship category."""
    for key in reversed(key_path):
        if key not in IGNORED_KEYS:
            return key
    return "technology"


async def _merge_repository_and_stack(
    tx: AsyncManagedTransaction,
    record: RepositoryStackRecord,
    items: Sequence[TechStackItem],
) -> dict[str, int]:
    """Merge one repository and its stack items within a single Neo4j transaction."""
    await tx.run(
        """
        MERGE (repo:Repository {id: $id})
        SET repo.repo_name = $repo_name,
            repo.github_url = $github_url
        """,
        id=str(record.repository_id),
        repo_name=record.repo_name,
        github_url=record.github_url,
    )

    unique_items = 0
    relationships = 0

    for item in items:
        if item.label == "Framework":
            await tx.run(
                """
                MATCH (repo:Repository {id: $repository_id})
                MERGE (stack:Framework {name: $name})
                SET stack.category = $category
                MERGE (repo)-[rel:USES]->(stack)
                SET rel.category = $category
                """,
                repository_id=str(record.repository_id),
                name=item.name,
                category=item.category,
            )
        else:
            await tx.run(
                """
                MATCH (repo:Repository {id: $repository_id})
                MERGE (stack:Technology {name: $name})
                SET stack.category = $category
                MERGE (repo)-[rel:USES]->(stack)
                SET rel.category = $category
                """,
                repository_id=str(record.repository_id),
                name=item.name,
                category=item.category,
            )

        unique_items += 1
        relationships += 1

    return {
        "unique_stack_items": unique_items,
        "uses_relationships": relationships,
    }


def _analysis_has_usable_stack(analysis: Analysis | None) -> bool:
    """Return True when an analysis row contains graph-worthy stack data."""
    return (
        analysis is not None
        and analysis.status == AnalysisStatus.COMPLETED.value
        and isinstance(analysis.tech_stack, Mapping)
        and bool(analysis.tech_stack)
    )


def _latest_usable_analysis_for_repository(
    repository_id: UUID,
    rows: Sequence[tuple[Repository, Analysis | None]],
) -> Analysis | None:
    """Find the latest completed analysis with tech_stack for one repository."""
    for repository, analysis in rows:
        if repository.id != repository_id:
            continue
        if _analysis_has_usable_stack(analysis):
            return analysis
    return None


def log_stack_breakdown(records: Sequence[RepositoryStackRecord]) -> None:
    """Print a concise per-repository extraction summary."""
    totals_by_label: dict[str, int] = defaultdict(int)

    for record in records:
        items = extract_tech_stack_items(record.tech_stack)
        for item in items:
            totals_by_label[item.label] += 1

        logger.info(
            "Prepared repo=%s id=%s extracted_stack_items=%d",
            record.repo_name,
            record.repository_id,
            len(items),
        )

    logger.info(
        "Prepared graph candidates: frameworks=%d technologies=%d",
        totals_by_label["Framework"],
        totals_by_label["Technology"],
    )


async def main() -> None:
    """Script entry point."""
    configure_logging()
    engine.echo = False
    logger.info("Starting Phase 1 repository graph ingestion.")

    graph_service = GraphService()
    if not graph_service.is_configured:
        raise RuntimeError("Neo4j is not configured. Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.")

    try:
        async with AsyncSessionLocal() as db:
            records = await fetch_repository_stack_records(db)

        logger.info("Fetched %d repositories from PostgreSQL.", len(records))
        log_stack_breakdown(records)

        await graph_service.verify_connection()
        await graph_service.initialize_constraints()

        stats = await ingest_repository_graph(graph_service, records)
        logger.info(
            "Graph ingestion complete: repositories_seen=%d repositories_ingested=%d "
            "repositories_without_stack=%d stack_nodes_merged=%d uses_edges_merged=%d",
            stats.repositories_seen,
            stats.repositories_ingested,
            stats.repositories_without_stack,
            stats.unique_stack_items,
            stats.uses_relationships,
        )
    finally:
        await graph_service.close()
        await engine.dispose()


def run() -> None:
    """
    Run the async migration.

    psycopg v3 async requires a SelectorEventLoop on Windows. Python 3.14
    supports passing a loop factory directly, which avoids the deprecated
    global event-loop policy APIs.
    """
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
        return

    asyncio.run(main())


if __name__ == "__main__":
    run()
