"""
app/main.py

FastAPI application entry point.

Endpoints implemented in this file (Phase 1, Step 1):
  POST /api/v1/analyze            — Submit a GitHub URL for analysis
  GET  /api/v1/repositories/{id}  — Get repository metadata
  GET  /api/v1/analyses/{id}      — Get analysis details (polling endpoint)
  GET  /health                    — Health check

All parser and AI steps (Steps 2 & 3) will be added in subsequent PRs.
"""

import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# psycopg v3 async requires SelectorEventLoop on Windows.
# Python 3.8+ defaults to ProactorEventLoop on Windows which psycopg does not support.
# Must be set before any asyncio or SQLAlchemy async engine code runs.
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.graph import router as graph_router
from app.api.v1.chat import router as chat_router
from app.api.v1.twin import router as twin_router
from app.api.research import router as research_router
from app.core.config import get_settings
from app.core.database import Base, engine, get_db
from app.models.repository import Analysis, AnalysisStatus, Repository
from app.schemas.repository import (
    AnalysisDetailResponse,
    AnalysisStatusResponse,
    AnalyzeRepoRequest,
    RepositoryResponse,
)
from app.services import github_service
from app.services.github_service import (
    GitHubAuthError,
    GitHubRateLimitError,
    GitHubServiceError,
    RepoNotFoundError,
)
from app.services.llm_service import generate_repo_summary
from app.services.parser_service import parser_service
from app.services.graph.graph_service import GraphService, GraphServiceError

# ─── Logging ──────────────────────────────────────────────────────────────────

settings = get_settings()
graph_service = GraphService(settings)
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


# ─── Lifespan (startup / shutdown) ────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    On startup:  Creates all database tables that don't yet exist.
                 (Alembic handles schema migrations; this is a safety net
                 for fresh environments where migrations haven't been run yet.)
    On shutdown: Disposes the async engine connection pool cleanly.
    """
    logger.info("DevTwin backend starting up…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified / created.")
    if graph_service.is_configured:
        try:
            await graph_service.verify_connection()
            await graph_service.initialize_constraints()
            app.state.graph_service = graph_service
            app.state.graph_ready = True
        except GraphServiceError as exc:
            logger.error(
                "Neo4j graph startup failed; graph features are disabled until "
                "the connection issue is fixed: %s",
                exc,
            )
            await graph_service.close()
            app.state.graph_service = None
            app.state.graph_ready = False
    else:
        logger.warning(
            "Neo4j credentials are not fully configured; skipping graph startup. "
            "Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD to enable Phase 3 graph services."
        )
        app.state.graph_service = None
        app.state.graph_ready = False
    try:
        yield
    finally:
        logger.info("DevTwin backend shutting down…")
        await graph_service.close()
        await engine.dispose()


# ─── App instance ─────────────────────────────────────────────────────────────


app = FastAPI(
    title="DevTwin — Repository Intelligence Engine",
    description=(
        "Phase 1 API: Submit a GitHub repository URL and receive a structured analysis "
        "including the file manifest, tech stack, dependency graph, and an AI-generated summary."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the Next.js frontend (localhost:3000) during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research_router)
app.include_router(graph_router)
app.include_router(twin_router)
app.include_router(chat_router)


# ─── Health check ─────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
async def health_check():
    """Lightweight health check endpoint. Returns 200 if the app is running."""
    return {"status": "ok", "version": "0.1.0"}


# ─── Analyze endpoint ─────────────────────────────────────────────────────────


@app.post(
    "/api/v1/analyze",
    response_model=AnalysisStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a GitHub repository for analysis",
    tags=["Analysis"],
)
async def analyze_repository(
    request: AnalyzeRepoRequest,
    db: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    """
    Submit a GitHub repository URL for analysis.

    **Fingerprinting / cache behaviour:**
    - If the repository has already been analysed at the same commit hash,
      returns the existing completed analysis immediately (`cached: true`).
    - If the repo exists but has new commits, a fresh analysis is created.
    - If the repo is new, a new Repository + Analysis row is created.

    **What happens here (Phase 1, Steps 2 & 3):**
    1. Validate and normalise the GitHub URL.
    2. Fetch repository metadata and the latest commit SHA from GitHub.
    3. Check the database for an existing analysis at this commit hash.
    4. If not cached: persist a new Repository row (or update existing),
       create a new Analysis row, fetch the file manifest, and save it.
    5. Run the parser layer — batch-fetch config/source files and run:
         - Tech stack detector   → tech_stack JSONB column
         - Route extractor       → api_routes JSONB column
         - AST dependency graph  → dependency_graph JSONB column
         - Architecture detector → architecture_hints JSONB column
    6. Run the Gemini summarizer over parser metadata only and save summary_json.
    7. Mark analysis as COMPLETED and return the analysis ID.
    """
    owner = request.owner
    repo = request.repo
    github_url = request.github_url

    logger.info("Analyze request: %s/%s", owner, repo)
    local_repo_path = None
    metadata = None

    # ── 1. Fetch metadata + commit SHA from GitHub ────────────────────────
    try:
        metadata, commit_hash, manifest = await github_service.fetch_repository(
            owner=owner,
            repo=repo,
            github_url=github_url,
        )
    except RepoNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except GitHubRateLimitError as e:
        if not settings.enable_clone:
            raise HTTPException(status_code=429, detail=str(e)) from e
        try:
            metadata, commit_hash, manifest, local_repo_path = (
                await github_service.fetch_repository_from_clone(
                    owner=owner,
                    repo=repo,
                    github_url=github_url,
                    metadata=metadata,
                )
            )
        except Exception as clone_exc:
            logger.exception(
                "GitHub API was rate-limited and local clone fallback failed for %s/%s: %s",
                owner,
                repo,
                clone_exc,
            )
            raise HTTPException(status_code=429, detail=str(e)) from e
    except GitHubServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # ── 2. Fingerprint check ──────────────────────────────────────────────
    # Look for a Repository row for this URL that already has a completed
    # Analysis at the same commit hash.
    existing_repo_stmt = select(Repository).where(Repository.github_url == github_url)
    existing_repo = (await db.execute(existing_repo_stmt)).scalar_one_or_none()

    if existing_repo is not None and existing_repo.latest_commit_hash == commit_hash:
        # Check for a completed analysis at this exact commit
        cached_analysis_stmt = (
            select(Analysis)
            .where(Analysis.repository_id == existing_repo.id)
            .where(Analysis.commit_hash == commit_hash)
            .where(Analysis.status == AnalysisStatus.COMPLETED.value)
            .where(Analysis.tech_stack.is_not(None))
            .where(Analysis.api_routes.is_not(None))
            .where(Analysis.dependency_graph.is_not(None))
            .where(Analysis.architecture_hints.is_not(None))
            .where(Analysis.summary_json.is_not(None))
            .order_by(Analysis.created_at.desc())
            .limit(1)
        )
        cached_analysis = (await db.execute(cached_analysis_stmt)).scalar_one_or_none()

        if cached_analysis is not None:
            logger.info(
                "Cache hit: %s/%s at %s — returning existing analysis %s",
                owner, repo, commit_hash[:8], str(cached_analysis.id)[:8],
            )
            return AnalysisStatusResponse(
                analysis_id=cached_analysis.id,
                repository_id=existing_repo.id,
                status=cached_analysis.status,
                cached=True,
                message=(
                    f"A completed analysis already exists for this repository at commit "
                    f"{commit_hash[:8]}. No re-analysis triggered."
                ),
            )

    # ── 3. Upsert the Repository row ──────────────────────────────────────
    if existing_repo is None:
        repo_row = Repository(
            github_url=github_url,
            owner=owner,
            repo_name=repo,
            default_branch=metadata["default_branch"],
            description=metadata.get("description"),
            stars=metadata.get("stars", 0),
            primary_language=metadata.get("primary_language"),
            is_private=metadata.get("is_private", False),
            latest_commit_hash=commit_hash,
        )
        db.add(repo_row)
        logger.info("Created new Repository row for %s/%s", owner, repo)
    else:
        # Update metadata fields in case they changed (stars, description, etc.)
        existing_repo.default_branch = metadata["default_branch"]
        existing_repo.description = metadata.get("description")
        existing_repo.stars = metadata.get("stars", 0)
        existing_repo.primary_language = metadata.get("primary_language")
        existing_repo.is_private = metadata.get("is_private", False)
        existing_repo.latest_commit_hash = commit_hash
        repo_row = existing_repo
        logger.info("Updated Repository row for %s/%s (new commit %s)", owner, repo, commit_hash[:8])

    # Flush to get the repo_row.id (needed for the FK in Analysis)
    await db.flush()

    # ── 4. Create Analysis row (RUNNING state) ───────────────────────────
    analysis_row = Analysis(
        repository_id=repo_row.id,
        status=AnalysisStatus.RUNNING.value,
        commit_hash=commit_hash,
        # Store the raw file manifest immediately so it is never lost even
        # if the parser step below raises an exception.
        file_manifest=manifest.model_dump(),
    )
    db.add(analysis_row)
    await db.flush()  # get analysis_row.id without committing yet

    # ── 5. Run parser layer ───────────────────────────────────────────────
    # The parser service batch-fetches config/source files from the GitHub
    # Contents API, runs all four detectors, and returns structured results.
    try:
        if local_repo_path is None and settings.enable_clone:
            try:
                local_repo_path = await github_service.clone_repo(github_url, commit_hash)
            except Exception as clone_exc:
                logger.warning(
                    "Local clone unavailable for %s/%s; parser will use GitHub API: %s",
                    owner,
                    repo,
                    clone_exc,
                )

        parser_result = await parser_service.run(
            manifest=manifest,
            owner=owner,
            repo=repo,
            commit_sha=commit_hash,
            local_repo_path=local_repo_path,
        )

        # Persist all four detector outputs as JSONB
        analysis_row.tech_stack = parser_result.tech_stack.model_dump()
        analysis_row.api_routes = parser_result.routes.model_dump()
        analysis_row.dependency_graph = parser_result.dependency_graph.model_dump()
        analysis_row.architecture_hints = parser_result.architecture.model_dump()

        # Step 3: AI summarizer. The service receives only structured parser
        # metadata and returns a safe fallback if Gemini is unavailable.
        summary_json = await generate_repo_summary(
            {
                "file_manifest": manifest.model_dump(),
                "tech_stack": analysis_row.tech_stack,
                "api_routes": analysis_row.api_routes,
                "dependency_graph": analysis_row.dependency_graph,
                "architecture_hints": analysis_row.architecture_hints,
                "files_fetched": parser_result.files_fetched,
                "budget_exhausted": parser_result.budget_exhausted,
            }
        )
        analysis_row.summary_json = summary_json
        analysis_row.summary = summary_json.get("project_purpose")

        analysis_row.status = AnalysisStatus.COMPLETED.value
        analysis_row.completed_at = datetime.now(timezone.utc)

        logger.info(
            "Parser and summarizer complete: %s/%s — %d frameworks, %d routes, "
            "%d graph nodes, pattern='%s'",
            owner, repo,
            len(parser_result.tech_stack.frameworks),
            parser_result.routes.total_routes,
            len(parser_result.dependency_graph.nodes),
            parser_result.architecture.primary_pattern,
        )
        parser_message = (
            f"Repository {owner}/{repo} fully analysed at commit {commit_hash[:8]}. "
            f"Tech stack: {', '.join(parser_result.tech_stack.frameworks) or 'n/a'}. "
            f"Routes: {parser_result.routes.total_routes}. "
            f"Architecture: {parser_result.architecture.primary_pattern}. "
            "AI summary saved."
        )

    except Exception as exc:
        # Parser failures should NOT discard the manifest — mark as FAILED
        # so the user can retry without losing the Step 1 data.
        logger.exception("Parser layer failed for %s/%s: %s", owner, repo, exc)
        analysis_row.status = AnalysisStatus.FAILED.value
        analysis_row.error_message = str(exc)
        analysis_row.completed_at = datetime.now(timezone.utc)
        parser_message = (
            f"Manifest fetched for {owner}/{repo} at commit {commit_hash[:8]}, "
            f"but the parser layer encountered an error: {exc}"
        )

    await db.commit()
    await db.refresh(analysis_row)

    logger.info(
        "Analysis saved: id=%s status=%s repo=%s/%s commit=%s",
        str(analysis_row.id)[:8],
        analysis_row.status,
        owner, repo, commit_hash[:8],
    )

    return AnalysisStatusResponse(
        analysis_id=analysis_row.id,
        repository_id=repo_row.id,
        status=analysis_row.status,
        cached=False,
        message=parser_message,
    )


# ─── Repository detail endpoint ───────────────────────────────────────────────


@app.get(
    "/api/v1/repositories/{repository_id}",
    response_model=RepositoryResponse,
    summary="Get repository metadata",
    tags=["Repositories"],
)
async def get_repository(
    repository_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RepositoryResponse:
    """Fetch a repository record by ID."""
    stmt = select(Repository).where(Repository.id == repository_id)
    repo = (await db.execute(stmt)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found.")
    return RepositoryResponse.model_validate(repo)


# ─── Analysis detail / polling endpoint ──────────────────────────────────────


@app.get(
    "/api/v1/analyses/{analysis_id}",
    response_model=AnalysisDetailResponse,
    summary="Get analysis details",
    tags=["Analysis"],
)
async def get_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AnalysisDetailResponse:
    """
    Fetch the full details of an analysis by ID.

    Poll this endpoint after submitting a POST /api/v1/analyze to check
    progress and retrieve results when status='completed'.
    """
    stmt = select(Analysis).where(Analysis.id == analysis_id)
    analysis = (await db.execute(stmt)).scalar_one_or_none()
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found.")
    return AnalysisDetailResponse.model_validate(analysis)
