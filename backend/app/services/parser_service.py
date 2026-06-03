"""
app/services/parser_service.py

Parser orchestrator — Step 2 of the DevTwin Repository Intelligence Engine.

Responsibilities:
  1. Determine which files need to be fetched for each detector.
  2. Deduplicate the fetch list to minimise GitHub API calls.
  3. Batch-fetch file contents via the GitHub Contents API.
  4. Run all four detectors in the correct order.
  5. Return a single unified ParserResult dict.

Fetch budget:
  Without a GITHUB_TOKEN the GitHub API allows 60 requests/hour.
  Step 1 (manifest fetch) already consumed ~3 calls:
    - GET /repos/{owner}/{repo}             (metadata)
    - GET /repos/{owner}/{repo}/commits/... (latest commit)
    - GET /repos/{owner}/{repo}/git/trees/... (file tree)
  That leaves ~57 calls for the parser layer per analysis.
  MAX_TOTAL_FETCH caps the total at FETCH_BUDGET_CAP to stay safe.

Orchestration order:
  1. Architecture detection  — free (path-only, zero API calls)
  2. Build fetch list        — union of tech stack + route + AST candidates
  3. Batch fetch             — concurrent, semaphore-bounded
  4. Tech stack detection    — uses dependency files (requirements.txt, etc.)
  5. Route extraction        — uses Python/TS source files
  6. AST dependency graph    — uses Python source files

All four detectors are stateless singletons. They run sequentially but
share the same pre-fetched file_contents dict — no file is fetched twice.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from app.parsers.architecture_service import ArchitectureResult, architecture_service
from app.parsers.ast_service import DependencyGraphResult, ast_service
from app.parsers.route_service import RouteExtractionResult, route_service
from app.parsers.tech_stack_detector import TechStackResult, tech_stack_detector
from app.schemas.manifest import RepoManifest
from app.services.github_service import get_file_contents_batch

logger = logging.getLogger(__name__)

# ── Budget ─────────────────────────────────────────────────────────────────────

# Maximum files to fetch in total per analysis run.
# Keeps unauthenticated usage safe (60 req/hr - ~3 used by manifest fetch = 57).
# With a GITHUB_TOKEN (5000 req/hr) this can be raised significantly.
FETCH_BUDGET_CAP: int = 50

# Dependency/config file basenames checked for tech stack detection.
# These are always fetched first (if they exist) since they're small and critical.
_TECH_STACK_FILENAMES: frozenset[str] = frozenset({
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "Pipfile",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "Gemfile",
})


# ── Output schema ──────────────────────────────────────────────────────────────


class ParserResult(BaseModel):
    """
    Unified output of the parser orchestrator.

    Each field maps directly to a JSONB column on the Analysis model:
      tech_stack        → Analysis.tech_stack
      routes            → Analysis.api_routes
      dependency_graph  → Analysis.dependency_graph
      architecture      → Analysis.architecture_hints

    The model serialises cleanly with .model_dump() for SQLAlchemy JSONB storage.
    """

    tech_stack: TechStackResult = Field(
        description="Languages, frameworks, databases, and AI tools detected."
    )
    routes: RouteExtractionResult = Field(
        description="API routes detected from FastAPI decorators and Next.js handlers."
    )
    dependency_graph: DependencyGraphResult = Field(
        description="File-level Python import dependency graph."
    )
    architecture: ArchitectureResult = Field(
        description="Heuristic architectural pattern detection result."
    )
    files_fetched: int = Field(
        default=0,
        description="Total number of files fetched from the GitHub Contents API.",
    )
    budget_exhausted: bool = Field(
        default=False,
        description=(
            "True if the candidate file list exceeded FETCH_BUDGET_CAP. "
            "Some lower-priority files may have been skipped."
        ),
    )


# ── Orchestrator ───────────────────────────────────────────────────────────────


class ParserService:
    """
    Stateless parser orchestrator.

    Coordinates the four detectors and manages the GitHub file-fetch budget.
    """

    async def run(
        self,
        manifest: RepoManifest,
        owner: str,
        repo: str,
        commit_sha: str,
        local_repo_path: Path | None = None,
    ) -> ParserResult:
        """
        Run the full parser pipeline for a repository.

        Args:
            manifest:    The RepoManifest built by the GitHub service in Step 1.
            owner:       Repository owner (e.g. "tiangolo").
            repo:        Repository name (e.g. "fastapi").
            commit_sha:  The HEAD commit SHA — used as the ref for content fetches
                         to ensure determinism (same commit = same result).
            local_repo_path:
                         Optional path to a local clone. When provided, parser
                         content is read from disk instead of the GitHub
                         Contents API, avoiding unauthenticated rate limits.

        Returns:
            A ParserResult containing all four detector outputs.
        """
        logger.info(
            "ParserService.run() started for %s/%s@%s",
            owner, repo, commit_sha[:8],
        )

        # ── Step 1: Architecture (free — no fetch needed) ─────────────────────
        logger.info("[1/4] Running architecture detector …")
        architecture = architecture_service.detect(manifest)

        # ── Step 2: Build fetch list ──────────────────────────────────────────
        logger.info("[2/4] Building file fetch list …")
        fetch_paths, budget_exhausted = self._build_fetch_list(manifest)

        # ── Step 3: Batch-fetch file contents ─────────────────────────────────
        file_contents: dict[str, str] = {}
        if fetch_paths:
            if local_repo_path is not None:
                logger.info(
                    "[3/4] Reading %d file(s) from local clone: %s",
                    len(fetch_paths),
                    local_repo_path,
                )
                file_contents = self._read_local_file_contents(local_repo_path, fetch_paths)
            else:
                logger.info(
                    "[3/4] Fetching %d file(s) from GitHub Contents API …",
                    len(fetch_paths),
                )
                file_contents = await get_file_contents_batch(
                    owner, repo, list(fetch_paths), commit_sha,
                    max_concurrent=8,
                )

        # ── Step 4: Run content-dependent detectors ───────────────────────────
        logger.info("[4/4] Running content detectors …")

        # Tech stack: pass only dependency/config file contents
        tech_stack_contents = self._filter_contents(
            file_contents, lambda p: Path(p).name in _TECH_STACK_FILENAMES
        )
        tech_stack = tech_stack_detector.detect(manifest, tech_stack_contents)

        # Route extraction: pass only Python + JS/TS files
        route_candidates = set(route_service.get_candidate_files(manifest))
        route_contents = self._filter_contents(
            file_contents, lambda p: p in route_candidates
        )
        routes = route_service.detect(manifest, route_contents)

        # AST graph: pass only Python files
        ast_candidates = set(ast_service.get_candidate_files(manifest))
        ast_contents = self._filter_contents(
            file_contents, lambda p: p in ast_candidates
        )
        dependency_graph = ast_service.build_graph(manifest, ast_contents)

        logger.info(
            "ParserService.run() complete for %s/%s: "
            "tech=%d frameworks, routes=%d, nodes=%d, pattern='%s'",
            owner, repo,
            len(tech_stack.frameworks),
            routes.total_routes,
            len(dependency_graph.nodes),
            architecture.primary_pattern,
        )

        return ParserResult(
            tech_stack=tech_stack,
            routes=routes,
            dependency_graph=dependency_graph,
            architecture=architecture,
            files_fetched=len(file_contents),
            budget_exhausted=budget_exhausted,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_fetch_list(
        self,
        manifest: RepoManifest,
    ) -> tuple[set[str], bool]:
        """
        Build the deduplicated list of files to fetch, within the budget cap.

        Priority order (higher priority filled first):
          1. Tech stack config files (requirements.txt, package.json, etc.)
          2. Route extraction candidates (routers/, app/api/route.ts, etc.)
          3. AST dependency graph candidates (Python files, prioritised)

        Returns:
            (fetch_set, budget_exhausted)
            budget_exhausted is True if candidates exceeded FETCH_BUDGET_CAP.
        """
        manifest_paths = {entry.path for entry in manifest.files}
        fetch: list[str] = []

        # Tier 1: Tech stack files (highest value per call)
        for path in manifest_paths:
            if Path(path).name in _TECH_STACK_FILENAMES:
                fetch.append(path)

        # Tier 2: Route candidates
        for path in route_service.get_candidate_files(manifest):
            if path not in fetch:
                fetch.append(path)

        # Tier 3: AST candidates (already prioritised by ast_service)
        for path in ast_service.get_candidate_files(manifest):
            if path not in fetch:
                fetch.append(path)

        budget_exhausted = len(fetch) > FETCH_BUDGET_CAP
        capped = fetch[:FETCH_BUDGET_CAP]

        if budget_exhausted:
            logger.warning(
                "Fetch list (%d files) exceeded budget cap (%d). "
                "%d file(s) skipped. Add a GITHUB_TOKEN to raise the limit.",
                len(fetch), FETCH_BUDGET_CAP, len(fetch) - FETCH_BUDGET_CAP,
            )

        logger.info(
            "Fetch list: %d tier-1 (tech stack) + %d total = %d (capped at %d)",
            sum(1 for p in capped if Path(p).name in _TECH_STACK_FILENAMES),
            len(capped),
            len(fetch),
            FETCH_BUDGET_CAP,
        )

        return set(capped), budget_exhausted

    @staticmethod
    def _filter_contents(
        file_contents: dict[str, str],
        predicate: Any,
    ) -> dict[str, str]:
        """Return a subset of file_contents where predicate(path) is True."""
        return {path: content for path, content in file_contents.items() if predicate(path)}

    @staticmethod
    def _read_local_file_contents(
        repo_root: Path,
        paths: set[str],
    ) -> dict[str, str]:
        """
        Read parser candidate files from a local clone.

        This deliberately reads only the bounded parser fetch list, not the
        whole repo. Binary and oversized files are skipped.
        """
        fetched: dict[str, str] = {}
        root = repo_root.resolve()

        for repo_path in paths:
            candidate = root.joinpath(*PurePosixPath(repo_path).parts).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                logger.warning("Skipping path outside clone root: %s", repo_path)
                continue

            if not candidate.is_file():
                continue

            try:
                if candidate.stat().st_size > 1_000_000:
                    logger.debug("Skipping large local file: %s", repo_path)
                    continue
                fetched[repo_path] = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.debug("Skipping non-UTF-8 local file: %s", repo_path)
            except OSError as exc:
                logger.warning("Could not read local file '%s': %s", repo_path, exc)

        logger.info("Local clone read complete: %d/%d files read", len(fetched), len(paths))
        return fetched


# ── Module-level singleton ─────────────────────────────────────────────────────
parser_service = ParserService()
