"""
app/schemas/repository.py

Pydantic v2 schemas for the Repository and Analysis API contracts.

These schemas define what the FastAPI endpoints accept and return.
They are intentionally kept separate from:
  - app/schemas/manifest.py  — the internal manifest sent to Gemini
  - app/models/repository.py — the SQLAlchemy ORM layer

This separation ensures the API surface can evolve independently from
the database schema and the AI input format.
"""

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Regex that matches https://github.com/<owner>/<repo>
# - Strips trailing slashes and .git suffix automatically
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


# ─── Request schemas ──────────────────────────────────────────────────────────


class AnalyzeRepoRequest(BaseModel):
    """
    Request body for POST /api/v1/analyze.

    Accepts a GitHub repository URL and submits it for analysis.
    """

    github_url: str = Field(
        ...,
        description=(
            "Full HTTPS URL of the GitHub repository to analyse. "
            "Must be in the format: https://github.com/<owner>/<repo>. "
            "Trailing slashes and .git suffixes are accepted and normalised."
        ),
        examples=[
            "https://github.com/tiangolo/fastapi",
            "https://github.com/vercel/next.js.git",
        ],
    )

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """Normalise and validate the GitHub URL."""
        v = v.strip()
        if not _GITHUB_URL_RE.match(v):
            raise ValueError(
                "Invalid GitHub URL. "
                "Expected format: https://github.com/<owner>/<repo>"
            )
        # Normalise: strip trailing slash and .git
        v = v.rstrip("/")
        if v.endswith(".git"):
            v = v[:-4]
        return v

    @property
    def owner(self) -> str:
        """Extract the repository owner from the URL."""
        m = _GITHUB_URL_RE.match(self.github_url)
        assert m, "URL already validated"  # noqa: S101
        return m.group("owner")

    @property
    def repo(self) -> str:
        """Extract the repository name from the URL."""
        m = _GITHUB_URL_RE.match(self.github_url)
        assert m, "URL already validated"  # noqa: S101
        return m.group("repo")


# ─── Repository response schemas ──────────────────────────────────────────────


class RepositoryResponse(BaseModel):
    """Serialised representation of a Repository row returned by the API."""

    model_config = {"from_attributes": True}

    id: uuid.UUID = Field(..., description="Unique identifier for this repository record.")
    github_url: str = Field(..., description="Canonical GitHub URL of the repository.")
    owner: str = Field(..., description="GitHub username or organisation.")
    repo_name: str = Field(..., description="Repository name.")
    default_branch: str = Field(..., description="Default branch (e.g. main, master).")
    description: str | None = Field(None, description="Repository description from GitHub.")
    stars: int = Field(..., description="Star count at the time of last fetch.")
    primary_language: str | None = Field(None, description="Primary programming language.")
    is_private: bool = Field(..., description="True if the repository is private.")
    latest_commit_hash: str | None = Field(
        None,
        description="SHA of the most recently analysed HEAD commit.",
    )
    created_at: datetime = Field(..., description="When this record was first created (UTC).")
    updated_at: datetime = Field(..., description="When this record was last updated (UTC).")


# ─── Analysis response schemas ────────────────────────────────────────────────


class AnalysisStatusResponse(BaseModel):
    """
    Lightweight response for POST /api/v1/analyze.

    Returned immediately after submission — the client should poll
    GET /api/v1/analyses/{analysis_id} to track progress.
    """

    analysis_id: uuid.UUID = Field(..., description="ID to use for polling analysis progress.")
    repository_id: uuid.UUID = Field(..., description="ID of the associated repository record.")
    status: str = Field(
        ...,
        description="Initial status of the analysis. Always 'pending' for new submissions.",
    )
    cached: bool = Field(
        ...,
        description=(
            "True if a completed analysis already exists for this repository at the same "
            "commit hash. When True, no new processing is triggered and analysis_id points "
            "to the existing completed analysis."
        ),
    )
    message: str = Field(
        ...,
        description="Human-readable summary of the submission outcome.",
    )


class AnalysisDetailResponse(BaseModel):
    """
    Full analysis response for GET /api/v1/analyses/{analysis_id}.

    Returned after the analysis has completed (or failed).
    """

    model_config = {"from_attributes": True}

    id: uuid.UUID = Field(..., description="Analysis ID.")
    repository_id: uuid.UUID = Field(..., description="Associated repository ID.")
    status: str = Field(..., description="Current lifecycle status of the analysis.")
    commit_hash: str | None = Field(
        None,
        description="The commit SHA that was analysed.",
    )
    error_message: str | None = Field(
        None,
        description="Error details if status='failed'. Null otherwise.",
    )

    # Structured parser outputs — populated incrementally across Steps 2 & 3
    file_manifest: dict[str, Any] | None = Field(
        None,
        description=(
            "Raw file tree manifest. "
            "Structure matches app/schemas/manifest.py::RepoManifest. "
            "Available after Step 1 completes."
        ),
    )
    tech_stack: dict[str, Any] | None = Field(
        None,
        description="Detected tech stack. Available after Step 2 completes.",
    )
    api_routes: dict[str, Any] | None = Field(
        None,
        description="Extracted API routes. Available after Step 2 completes.",
    )
    dependency_graph: dict[str, Any] | None = Field(
        None,
        description="File-level dependency graph. Available after Step 2 completes.",
    )
    architecture_hints: dict[str, Any] | None = Field(
        None,
        description="Heuristic architecture hints. Available after Step 2 completes.",
    )
    summary: str | None = Field(
        None,
        description=(
            "Legacy short text summary of the repository. "
            "Prefer summary_json for the full Step 3 output."
        ),
    )
    summary_json: dict[str, Any] | None = Field(
        None,
        description=(
            "Gemini-generated structured repository summary with project_purpose, "
            "architecture_summary, key_features, and strengths_and_weaknesses. "
            "Available after Step 3 completes."
        ),
    )
    created_at: datetime = Field(..., description="When this analysis was created (UTC).")
    completed_at: datetime | None = Field(
        None,
        description="When this analysis finished (UTC). Null if still in progress.",
    )
