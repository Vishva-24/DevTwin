"""
app/schemas/manifest.py

Internal Pydantic schemas for the Repository Manifest.

IMPORTANT — Architectural contract:
  These schemas represent the ONLY data structure that will ever be sent
  to Gemini. Raw source code is never sent — only this structured manifest.

  The heavy `description=` annotations on every field serve a dual purpose:
    1. Human documentation for engineers reading the code.
    2. They will be embedded verbatim in the Gemini prompt context in Step 3,
       helping the model understand what each field represents without needing
       extra prompt engineering.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ─── File tree node ───────────────────────────────────────────────────────────


class FileEntry(BaseModel):
    """
    Represents a single node in the repository's file tree.

    Only file-type nodes (not directories) are included in the manifest,
    because directories add noise without adding analytical value.
    """

    path: str = Field(
        ...,
        description=(
            "Relative path of the file within the repository root. "
            "Uses forward slashes regardless of OS. "
            "Example: 'src/api/routes/users.py'"
        ),
        examples=["src/api/routes/users.py", "package.json", "README.md"],
    )
    size: int = Field(
        ...,
        description=(
            "File size in bytes as reported by the GitHub API. "
            "Zero-byte files are included. Large files (>1MB) may indicate "
            "binary assets or auto-generated files."
        ),
        ge=0,
    )
    node_type: Literal["blob", "tree"] = Field(
        ...,
        description=(
            "GitHub tree node type. 'blob' = file, 'tree' = directory. "
            "Only 'blob' entries are included in the manifest."
        ),
    )


# ─── Top-level manifest ───────────────────────────────────────────────────────


class RepoManifest(BaseModel):
    """
    A structured, code-free summary of a GitHub repository.

    This is the canonical artifact produced by the GitHub fetching service
    and consumed by:
      - The Tech Stack Detector (Step 2): reads file paths and extensions.
      - The Route Extractor (Step 2): identifies route-file candidates.
      - The AST Service (Step 2): uses file paths to drive local parsing.
      - The LLM Summarizer (Step 3): receives this manifest as structured
        context to generate the human-readable summary.

    NEVER include raw file contents in this schema.
    """

    owner: str = Field(
        ...,
        description="GitHub username or organisation that owns the repository.",
        examples=["tiangolo", "vercel", "microsoft"],
    )
    repo_name: str = Field(
        ...,
        description="Repository name as it appears in the GitHub URL.",
        examples=["fastapi", "next.js", "TypeScript"],
    )
    description: str | None = Field(
        default=None,
        description=(
            "Repository description as written by the owner. "
            "May be null if the owner hasn't set one. "
            "Useful for Gemini to understand the project's stated purpose."
        ),
    )
    default_branch: str = Field(
        ...,
        description=(
            "The default branch of the repository (e.g. 'main', 'master', 'develop'). "
            "All manifest data is fetched from this branch."
        ),
    )
    primary_language: str | None = Field(
        default=None,
        description=(
            "Primary programming language as detected by GitHub's Linguist. "
            "Used as a coarse hint — the Tech Stack Detector will refine this."
        ),
        examples=["Python", "TypeScript", "Go", "Rust"],
    )
    stars: int = Field(
        default=0,
        description="Number of GitHub stars at the time of the last fetch.",
        ge=0,
    )
    commit_hash: str = Field(
        ...,
        description=(
            "SHA-1 hash (40 hex characters) of the HEAD commit on the default branch "
            "at the time this manifest was generated. "
            "Used as a cache key: if this hash matches the stored hash, the repository "
            "has not changed and re-analysis can be skipped."
        ),
        min_length=40,
        max_length=40,
        examples=["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"],
    )
    total_files: int = Field(
        ...,
        description=(
            "Total number of files (blobs) in the repository tree. "
            "Directories are not counted."
        ),
        ge=0,
    )
    files: list[FileEntry] = Field(
        ...,
        description=(
            "Flat list of every file in the repository tree. "
            "Ordered alphabetically by path. "
            "This is the primary input for all parser modules in Step 2."
        ),
    )

    @property
    def file_paths(self) -> list[str]:
        """Convenience: return just the list of file paths."""
        return [f.path for f in self.files]

    @property
    def extensions(self) -> set[str]:
        """Convenience: return the set of unique file extensions present."""
        result = set()
        for f in self.files:
            if "." in f.path:
                result.add(f.path.rsplit(".", 1)[-1].lower())
        return result

    def files_by_extension(self, ext: str) -> list[FileEntry]:
        """Return all FileEntry objects with the given extension (without dot)."""
        return [f for f in self.files if f.path.endswith(f".{ext}")]
