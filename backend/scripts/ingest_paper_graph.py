"""
scripts/ingest_paper_graph.py

Standalone migration script for Phase 3:
ingest Phase 2 Research Brain manifests from PostgreSQL into Neo4j.

Run from the backend directory:
    .\\.venv\\Scripts\\python.exe scripts\\ingest_paper_graph.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Mapping, Sequence
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
from app.models.research import Paper  # noqa: E402
from app.services.graph.graph_service import GraphService  # noqa: E402

logger = logging.getLogger("ingest_paper_graph")

TechNodeLabel = Literal["Technology", "Framework"]

FRAMEWORK_HINTS = {
    "angular",
    "bookdown",
    "django",
    "fastapi",
    "flask",
    "keras",
    "langchain",
    "llamaindex",
    "matplotlib",
    "next.js",
    "nextjs",
    "numpy",
    "pandas",
    "plotly",
    "pymupdf",
    "pymupdf4llm",
    "pytorch",
    "rmarkdown",
    "scikit-learn",
    "seaborn",
    "sentence-transformers",
    "spacy",
    "spring",
    "streamlit",
    "tensorflow",
    "transformers",
}

TECHNOLOGY_HINTS = {
    "aws",
    "azure",
    "bolt",
    "docker",
    "gemini",
    "github",
    "google cloud",
    "linux",
    "neo4j",
    "postgresql",
    "power bi",
    "python",
    "qdrant",
    "redis",
    "typescript",
    "windows",
}


@dataclass(frozen=True, slots=True)
class PaperManifestRecord:
    """Paper row plus its structured research manifest."""

    paper_id: UUID
    title: str | None
    filename: str
    research_manifest: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TechMention:
    """A framework or technology mentioned by a paper manifest."""

    name: str
    label: TechNodeLabel


@dataclass(frozen=True, slots=True)
class PaperGraphStats:
    """High-level paper graph migration counts."""

    papers_seen: int = 0
    papers_ingested: int = 0
    papers_skipped: int = 0
    concept_nodes_merged: int = 0
    concept_edges_merged: int = 0
    technology_nodes_merged: int = 0
    technology_edges_merged: int = 0
    framework_nodes_merged: int = 0
    framework_edges_merged: int = 0


def configure_logging() -> None:
    """Configure script logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


async def fetch_paper_manifest_records(db: AsyncSession) -> list[PaperManifestRecord]:
    """Fetch papers with a non-null research_manifest JSONB payload."""
    stmt = (
        select(Paper)
        .where(Paper.research_manifest.is_not(None))
        .order_by(Paper.uploaded_at.asc())
    )
    papers = (await db.execute(stmt)).scalars().all()

    records: list[PaperManifestRecord] = []
    for paper in papers:
        if not isinstance(paper.research_manifest, Mapping):
            logger.warning(
                "Skipping paper_id=%s title=%r: research_manifest is not a JSON object.",
                paper.id,
                paper.title,
            )
            continue

        records.append(
            PaperManifestRecord(
                paper_id=paper.id,
                title=paper.title,
                filename=paper.file_name,
                research_manifest=paper.research_manifest,
            )
        )

    return records


async def ingest_paper_graph(
    graph_service: GraphService,
    records: Sequence[PaperManifestRecord],
) -> PaperGraphStats:
    """Write Paper, Concept, Technology/Framework nodes and relationships to Neo4j."""
    papers_ingested = 0
    papers_skipped = 0
    concept_nodes_total = 0
    concept_edges_total = 0
    technology_nodes_total = 0
    technology_edges_total = 0
    framework_nodes_total = 0
    framework_edges_total = 0

    async with graph_service.driver.session() as session:
        for record in records:
            concepts = extract_core_concepts(record.research_manifest)
            tech_mentions = extract_tech_mentions(record.research_manifest)

            if not concepts and not tech_mentions:
                papers_skipped += 1
                logger.warning(
                    "Paper %s (%s) has no graph-worthy concepts or tech mentions.",
                    record.title or record.filename,
                    record.paper_id,
                )

            result = await session.execute_write(
                _merge_paper_manifest_graph,
                record,
                concepts,
                tech_mentions,
            )

            papers_ingested += 1
            concept_nodes_total += result["concept_nodes"]
            concept_edges_total += result["concept_edges"]
            technology_nodes_total += result["technology_nodes"]
            technology_edges_total += result["technology_edges"]
            framework_nodes_total += result["framework_nodes"]
            framework_edges_total += result["framework_edges"]

            logger.info(
                "Ingested paper=%r concepts=%d concept_edges=%d "
                "technologies=%d technology_edges=%d frameworks=%d framework_edges=%d",
                record.title or record.filename,
                result["concept_nodes"],
                result["concept_edges"],
                result["technology_nodes"],
                result["technology_edges"],
                result["framework_nodes"],
                result["framework_edges"],
            )

    return PaperGraphStats(
        papers_seen=len(records),
        papers_ingested=papers_ingested,
        papers_skipped=papers_skipped,
        concept_nodes_merged=concept_nodes_total,
        concept_edges_merged=concept_edges_total,
        technology_nodes_merged=technology_nodes_total,
        technology_edges_merged=technology_edges_total,
        framework_nodes_merged=framework_nodes_total,
        framework_edges_merged=framework_edges_total,
    )


def extract_core_concepts(research_manifest: Mapping[str, Any]) -> list[str]:
    """Extract normalized concept names from a ResearchManifest JSON object."""
    concepts = _extract_string_list(research_manifest.get("core_concepts"))
    return _dedupe_sorted(concepts)


def extract_tech_mentions(research_manifest: Mapping[str, Any]) -> list[TechMention]:
    """Extract normalized tech mentions from a ResearchManifest JSON object."""
    names = _dedupe_sorted(_extract_string_list(research_manifest.get("tech_stack_mentions")))
    mentions = [
        TechMention(
            name=name,
            label=classify_tech_mention(name),
        )
        for name in names
    ]
    return sorted(mentions, key=lambda item: (item.label, item.name.lower()))


def classify_tech_mention(name: str) -> TechNodeLabel:
    """Classify a manifest tech mention as Framework or Technology."""
    lowered = name.lower()

    if lowered in FRAMEWORK_HINTS:
        return "Framework"

    if lowered in TECHNOLOGY_HINTS:
        return "Technology"

    framework_keywords = ("library", "framework", "sdk", "package")
    if any(keyword in lowered for keyword in framework_keywords):
        return "Framework"

    return "Technology"


def normalize_graph_name(value: Any) -> str | None:
    """Normalize a concept or technology name into a stable Neo4j key."""
    if not isinstance(value, str):
        return None

    normalized = re.sub(r"\\s+", " ", value).strip()
    normalized = normalized.strip("`'\"[]{}()")
    if len(normalized) < 2:
        return None

    if normalized.lower() in {"none", "unknown", "n/a", "not applicable", "null"}:
        return None

    return normalized


def _extract_string_list(value: Any) -> list[str]:
    """Accept list-like manifest values and safely return normalized strings."""
    if value is None:
        return []

    if isinstance(value, str):
        normalized = normalize_graph_name(value)
        return [normalized] if normalized is not None else []

    if not isinstance(value, list | tuple | set):
        logger.warning("Expected list-like manifest field, received %s.", type(value).__name__)
        return []

    results: list[str] = []
    for item in value:
        normalized = normalize_graph_name(item)
        if normalized is not None:
            results.append(normalized)

    return results


def _dedupe_sorted(values: Sequence[str]) -> list[str]:
    """Deduplicate strings case-insensitively while preserving readable casing."""
    deduped: dict[str, str] = {}
    for value in values:
        deduped[value.lower()] = value
    return sorted(deduped.values(), key=str.lower)


async def _merge_paper_manifest_graph(
    tx: AsyncManagedTransaction,
    record: PaperManifestRecord,
    concepts: Sequence[str],
    tech_mentions: Sequence[TechMention],
) -> dict[str, int]:
    """Merge one paper and its manifest-derived graph objects in one transaction."""
    await tx.run(
        """
        MERGE (paper:Paper {id: $id})
        SET paper.title = $title,
            paper.filename = $filename
        """,
        id=str(record.paper_id),
        title=record.title,
        filename=record.filename,
    )

    concept_count = 0
    for concept in concepts:
        await tx.run(
            """
            MATCH (paper:Paper {id: $paper_id})
            MERGE (concept:Concept {name: $name})
            MERGE (paper)-[:EXPLAINS_CONCEPT]->(concept)
            """,
            paper_id=str(record.paper_id),
            name=concept,
        )
        concept_count += 1

    technology_count = 0
    framework_count = 0
    for mention in tech_mentions:
        if mention.label == "Framework":
            await tx.run(
                """
                MATCH (paper:Paper {id: $paper_id})
                MERGE (tech:Framework {name: $name})
                MERGE (paper)-[:MENTIONS_TECH]->(tech)
                """,
                paper_id=str(record.paper_id),
                name=mention.name,
            )
            framework_count += 1
            continue

        await tx.run(
            """
            MATCH (paper:Paper {id: $paper_id})
            MERGE (tech:Technology {name: $name})
            MERGE (paper)-[:MENTIONS_TECH]->(tech)
            """,
            paper_id=str(record.paper_id),
            name=mention.name,
        )
        technology_count += 1

    return {
        "concept_nodes": concept_count,
        "concept_edges": concept_count,
        "technology_nodes": technology_count,
        "technology_edges": technology_count,
        "framework_nodes": framework_count,
        "framework_edges": framework_count,
    }


def log_prepared_records(records: Sequence[PaperManifestRecord]) -> None:
    """Log extraction counts before writing to Neo4j."""
    concept_total = 0
    technology_total = 0
    framework_total = 0

    for record in records:
        concepts = extract_core_concepts(record.research_manifest)
        tech_mentions = extract_tech_mentions(record.research_manifest)
        technologies = [item for item in tech_mentions if item.label == "Technology"]
        frameworks = [item for item in tech_mentions if item.label == "Framework"]

        concept_total += len(concepts)
        technology_total += len(technologies)
        framework_total += len(frameworks)

        logger.info(
            "Prepared paper=%r id=%s concepts=%d technologies=%d frameworks=%d",
            record.title or record.filename,
            record.paper_id,
            len(concepts),
            len(technologies),
            len(frameworks),
        )

    logger.info(
        "Prepared graph candidates: papers=%d concepts=%d technologies=%d frameworks=%d",
        len(records),
        concept_total,
        technology_total,
        framework_total,
    )


async def main() -> None:
    """Script entry point."""
    configure_logging()
    engine.echo = False
    logger.info("Starting Phase 2 paper graph ingestion.")

    graph_service = GraphService()
    if not graph_service.is_configured:
        raise RuntimeError("Neo4j is not configured. Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.")

    try:
        async with AsyncSessionLocal() as db:
            records = await fetch_paper_manifest_records(db)

        logger.info("Fetched %d papers with research manifests from PostgreSQL.", len(records))
        log_prepared_records(records)

        await graph_service.verify_connection()
        await graph_service.initialize_constraints()

        stats = await ingest_paper_graph(graph_service, records)
        logger.info(
            "Paper graph ingestion complete: papers_seen=%d papers_ingested=%d "
            "papers_without_graph_items=%d concept_nodes_merged=%d concept_edges_merged=%d "
            "technology_nodes_merged=%d technology_edges_merged=%d "
            "framework_nodes_merged=%d framework_edges_merged=%d",
            stats.papers_seen,
            stats.papers_ingested,
            stats.papers_skipped,
            stats.concept_nodes_merged,
            stats.concept_edges_merged,
            stats.technology_nodes_merged,
            stats.technology_edges_merged,
            stats.framework_nodes_merged,
            stats.framework_edges_merged,
        )
    finally:
        await graph_service.close()
        await engine.dispose()


def run() -> None:
    """
    Run the async migration.

    psycopg v3 async requires a SelectorEventLoop on Windows. Python 3.14
    supports passing a loop factory directly, avoiding deprecated global policy APIs.
    """
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
        return

    asyncio.run(main())


if __name__ == "__main__":
    run()
