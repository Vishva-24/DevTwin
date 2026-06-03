"""
scripts/seed_user_graph.py

Seed the central User node for Phase 4 and attach existing repositories/papers.

Run from the backend directory:
    .\\.venv\\Scripts\\python.exe scripts\\seed_user_graph.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.graph.graph_service import GraphService  # noqa: E402

logger = logging.getLogger("seed_user_graph")

USER_ID = "me"
USER_NAME = "Developer"


def configure_logging() -> None:
    """Configure script logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


async def seed_user_graph() -> dict[str, Any]:
    """Create the User node and connect it to all current repositories and papers."""
    graph_service = GraphService()

    if not graph_service.is_configured:
        raise RuntimeError("Neo4j is not configured. Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.")

    try:
        await graph_service.verify_connection()
        await graph_service.initialize_constraints()

        async with graph_service.driver.session() as session:
            result = await session.run(
                _SEED_USER_QUERY,
                user_id=USER_ID,
                user_name=USER_NAME,
            )
            record = await result.single(strict=True)

        stats = {
            "user_id": record["user_id"],
            "user_name": record["user_name"],
            "repositories_connected": int(record["repositories_connected"] or 0),
            "papers_connected": int(record["papers_connected"] or 0),
        }
        logger.info(
            "Seeded User node id=%s repositories_connected=%d papers_connected=%d",
            stats["user_id"],
            stats["repositories_connected"],
            stats["papers_connected"],
        )
        return stats
    finally:
        await graph_service.close()


_SEED_USER_QUERY = """
MERGE (user:User {id: $user_id})
SET
  user.name = $user_name,
  user.updated_at = datetime()
WITH user
OPTIONAL MATCH (repo:Repository)
FOREACH (_ IN CASE WHEN repo IS NULL THEN [] ELSE [1] END |
  MERGE (user)-[:OWNER_OF]->(repo)
)
WITH user, count(DISTINCT repo) AS repositories_connected
OPTIONAL MATCH (paper:Paper)
FOREACH (_ IN CASE WHEN paper IS NULL THEN [] ELSE [1] END |
  MERGE (user)-[:READ]->(paper)
)
RETURN
  user.id AS user_id,
  user.name AS user_name,
  repositories_connected,
  count(DISTINCT paper) AS papers_connected
"""


async def main() -> None:
    """Script entry point."""
    configure_logging()
    stats = await seed_user_graph()
    logger.info("User graph seed complete: %s", stats)


def run() -> None:
    """Run the async seed with a Windows-compatible event loop."""
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
        return

    asyncio.run(main())


if __name__ == "__main__":
    run()
