"""
app/services/github_service.py

GitHub fetching service — the entry point for all data ingestion.

Responsibilities:
  1. get_repo_metadata()   — Fetch repository metadata from the GitHub REST API.
  2. get_latest_commit()   — Fetch the SHA of the HEAD commit (used for fingerprinting).
  3. get_file_manifest()   — Fetch the full recursive file tree via the GitHub Trees API.
  4. clone_repo()          — Perform a shallow git clone (optional, controlled by config).

Architectural rules enforced here:
  - No file *contents* are ever fetched. Only paths, sizes, and metadata.
  - Raw GitHub API responses are parsed into typed Pydantic models before
    being returned to the caller. No raw dicts leak out of this service.
  - All HTTP calls are made with httpx.AsyncClient for non-blocking I/O.
  - A single client instance is reused across calls (connection pooling).
"""

import logging
import subprocess
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.schemas.manifest import FileEntry, RepoManifest

logger = logging.getLogger(__name__)

settings = get_settings()

# ─── Exceptions ───────────────────────────────────────────────────────────────


class GitHubServiceError(Exception):
    """Base exception for all errors raised by the GitHub service."""


class RepoNotFoundError(GitHubServiceError):
    """Raised when the GitHub API returns 404 for a repository."""


class GitHubRateLimitError(GitHubServiceError):
    """Raised when the GitHub API rate limit is exceeded (HTTP 403/429)."""


class GitHubAuthError(GitHubServiceError):
    """Raised when the request is rejected due to authentication failure (HTTP 401)."""


class CloneError(GitHubServiceError):
    """Raised when git clone fails."""


# ─── GitHub API client factory ────────────────────────────────────────────────


def _build_headers() -> dict[str, str]:
    """
    Build HTTP headers for GitHub API requests.

    Includes the PAT if configured. Always requests the v3 JSON response format.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    else:
        logger.warning(
            "No GITHUB_TOKEN configured. API rate limit is 60 requests/hour. "
            "Set GITHUB_TOKEN in .env to raise the limit to 5,000/hour."
        )
    return headers


def _make_client() -> httpx.AsyncClient:
    """Create an httpx.AsyncClient configured for GitHub API calls."""
    return httpx.AsyncClient(
        base_url=settings.github_api_base,
        headers=_build_headers(),
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )


def _handle_github_response(response: httpx.Response, context: str) -> None:
    """
    Raise a typed exception based on the HTTP status code.

    Args:
        response: The httpx Response object.
        context:  Human-readable description of what was being fetched
                  (used in error messages).
    """
    if response.status_code == 200:
        return
    if response.status_code == 404:
        raise RepoNotFoundError(f"Not found while fetching {context}. "
                                "Check the URL and ensure the repo is public "
                                "(or that your token has the correct scope).")
    if response.status_code == 401:
        raise GitHubAuthError("GitHub API authentication failed. "
                              "Your GITHUB_TOKEN may be invalid or expired.")
    if response.status_code in (403, 429):
        reset = response.headers.get("x-ratelimit-reset", "unknown")
        raise GitHubRateLimitError(
            f"GitHub API rate limit exceeded. "
            f"Rate limit resets at Unix timestamp: {reset}. "
            "Set GITHUB_TOKEN in .env to increase the limit to 5,000/hour."
        )
    # Generic fallback
    response.raise_for_status()


# ─── Service functions ────────────────────────────────────────────────────────


async def get_repo_metadata(owner: str, repo: str) -> dict:
    """
    Fetch top-level repository metadata from the GitHub REST API.

    Endpoint: GET /repos/{owner}/{repo}

    Returns a dict containing the fields we care about:
      - name, description, default_branch, stargazers_count,
        language, private, topics

    Raises:
        RepoNotFoundError:     If the repo does not exist or is inaccessible.
        GitHubAuthError:       If the token is invalid.
        GitHubRateLimitError:  If the rate limit is exceeded.
    """
    async with _make_client() as client:
        url = f"/repos/{owner}/{repo}"
        logger.info("Fetching repo metadata: %s/%s", owner, repo)
        response = await client.get(url)
        _handle_github_response(response, f"repo metadata for {owner}/{repo}")

        data = response.json()
        return {
            "name": data["name"],
            "description": data.get("description"),
            "default_branch": data["default_branch"],
            "stars": data["stargazers_count"],
            "primary_language": data.get("language"),
            "is_private": data["private"],
            "topics": data.get("topics", []),
        }


async def get_latest_commit(owner: str, repo: str, branch: str) -> str:
    """
    Fetch the SHA-1 hash of the latest commit on a given branch.

    Endpoint: GET /repos/{owner}/{repo}/commits/{branch}

    This is used as the fingerprint key. If the stored hash matches the
    current hash, the repository has not changed and analysis can be skipped.

    Returns:
        40-character hex SHA string.

    Raises:
        RepoNotFoundError, GitHubAuthError, GitHubRateLimitError.
    """
    async with _make_client() as client:
        url = f"/repos/{owner}/{repo}/commits/{branch}"
        logger.info("Fetching latest commit for %s/%s on branch '%s'", owner, repo, branch)
        response = await client.get(url)
        _handle_github_response(response, f"latest commit for {owner}/{repo}@{branch}")

        sha: str = response.json()["sha"]
        logger.debug("Latest commit SHA: %s", sha)
        return sha


async def get_file_manifest(
    owner: str,
    repo: str,
    commit_sha: str,
    metadata: dict,
) -> RepoManifest:
    """
    Fetch the full recursive file tree and return a RepoManifest.

    Endpoint: GET /repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1

    IMPORTANT: This endpoint returns file paths and sizes ONLY.
    No file contents are fetched — that would violate the 'never send raw
    code to Gemini' rule and would also hit rate limits quickly.

    Large repositories (>100,000 blobs) will have `truncated: true` in
    the GitHub response. We log a warning but still return what we have.

    Args:
        owner:      Repository owner.
        repo:       Repository name.
        commit_sha: The commit SHA to fetch the tree at (for determinism).
        metadata:   The dict returned by get_repo_metadata().

    Returns:
        A fully populated RepoManifest Pydantic model.

    Raises:
        RepoNotFoundError, GitHubAuthError, GitHubRateLimitError.
    """
    async with _make_client() as client:
        url = f"/repos/{owner}/{repo}/git/trees/{commit_sha}"
        logger.info("Fetching file tree for %s/%s at %s", owner, repo, commit_sha[:8])
        response = await client.get(url, params={"recursive": "1"})
        _handle_github_response(response, f"file tree for {owner}/{repo}")

        data = response.json()

        if data.get("truncated", False):
            logger.warning(
                "GitHub returned a truncated file tree for %s/%s. "
                "The repository has more files than the GitHub Trees API can return in one call. "
                "Consider paginating or using sparse checkout for very large repos.",
                owner, repo,
            )

        # Filter to blob (file) nodes only — directories (trees) are not useful
        file_entries: list[FileEntry] = []
        for node in data.get("tree", []):
            if node["type"] == "blob":
                file_entries.append(
                    FileEntry(
                        path=node["path"],
                        size=node.get("size", 0),
                        node_type=node["type"],
                    )
                )

        # Sort alphabetically for deterministic output
        file_entries.sort(key=lambda f: f.path)

        manifest = RepoManifest(
            owner=owner,
            repo_name=repo,
            description=metadata.get("description"),
            default_branch=metadata["default_branch"],
            primary_language=metadata.get("primary_language"),
            stars=metadata.get("stars", 0),
            commit_hash=commit_sha,
            total_files=len(file_entries),
            files=file_entries,
        )

        logger.info(
            "Manifest built for %s/%s: %d files, %d unique extensions",
            owner, repo, manifest.total_files, len(manifest.extensions),
        )
        return manifest


async def clone_repo(github_url: str, commit_hash: str | None = None) -> Path:
    """
    Perform a shallow git clone of the repository to a local directory.

    Uses subprocess to call `git clone --depth=1` — avoids pulling the full
    history, which can be gigabytes for large repositories.

    The clone is stored at:
        {CLONE_BASE_DIR}/{owner}__{repo}__{commit_hash[:8]}/
        or {CLONE_BASE_DIR}/{owner}__{repo}__latest/ when commit_hash is unknown.

    If the directory already exists (from a previous run), the clone is
    skipped and the existing path is returned (idempotent).

    Args:
        github_url:   The full HTTPS GitHub URL.
        commit_hash:  The commit SHA (used as part of the directory name).
                      If None, clones the default branch into a "__latest" directory.

    Returns:
        Path to the cloned directory.

    Raises:
        CloneError: If git returns a non-zero exit code.
    """
    if not settings.enable_clone:
        logger.info("Cloning is disabled (ENABLE_CLONE=false). Skipping.")
        raise RuntimeError(
            "clone_repo() called but ENABLE_CLONE=false in settings. "
            "Check your .env file."
        )

    # Build a safe, unique directory name
    slug = github_url.rstrip("/").replace("https://github.com/", "").replace("/", "__")
    clone_suffix = commit_hash[:8] if commit_hash else "latest"
    clone_dir = Path(settings.clone_base_dir) / f"{slug}__{clone_suffix}"

    if clone_dir.exists():
        logger.info("Clone directory already exists, skipping clone: %s", clone_dir)
        return clone_dir

    clone_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git", "clone",
        "--depth", "1",       # shallow clone — only the latest snapshot
        "--single-branch",    # don't fetch all branches
        github_url,
        str(clone_dir),
    ]

    logger.info("Cloning %s → %s", github_url, clone_dir)
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5-minute timeout for large repos
    )

    if result.returncode != 0:
        raise CloneError(
            f"git clone failed for {github_url}.\n"
            f"Exit code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    logger.info("Clone completed: %s", clone_dir)
    return clone_dir


def get_clone_commit_hash(clone_dir: Path) -> str:
    """Return the HEAD SHA for a local clone."""
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise CloneError(
            f"git rev-parse failed for {clone_dir}.\n"
            f"Exit code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    return result.stdout.strip()


def build_manifest_from_clone(
    clone_dir: Path,
    owner: str,
    repo: str,
    commit_hash: str,
    metadata: dict | None = None,
) -> RepoManifest:
    """
    Build the same code-free manifest from a local clone.

    Only relative paths, sizes, and metadata are captured. Raw file contents
    remain outside the manifest and are not sent to Gemini.
    """
    metadata = metadata or {}
    file_entries: list[FileEntry] = []

    for path in clone_dir.rglob("*"):
        if not path.is_file():
            continue

        relative = path.relative_to(clone_dir)
        if ".git" in relative.parts:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            logger.debug("Could not stat local cloned file: %s", path)
            continue

        file_entries.append(
            FileEntry(
                path=relative.as_posix(),
                size=size,
                node_type="blob",
            )
        )

    file_entries.sort(key=lambda f: f.path)

    return RepoManifest(
        owner=owner,
        repo_name=repo,
        description=metadata.get("description"),
        default_branch=metadata.get("default_branch", "main"),
        primary_language=metadata.get("primary_language"),
        stars=metadata.get("stars", 0),
        commit_hash=commit_hash,
        total_files=len(file_entries),
        files=file_entries,
    )


async def fetch_repository_from_clone(
    owner: str,
    repo: str,
    github_url: str,
    metadata: dict | None = None,
) -> tuple[dict, str, RepoManifest, Path]:
    """
    Fallback ingestion path for public repos when the GitHub REST quota is exhausted.
    """
    logger.info("Falling back to local git clone ingestion for %s/%s", owner, repo)
    clone_dir = await clone_repo(github_url, None)
    commit_hash = get_clone_commit_hash(clone_dir)

    fallback_metadata = {
        "name": repo,
        "description": None,
        "default_branch": "main",
        "stars": 0,
        "primary_language": None,
        "is_private": False,
        "topics": [],
    }
    fallback_metadata.update(metadata or {})

    manifest = build_manifest_from_clone(
        clone_dir=clone_dir,
        owner=owner,
        repo=repo,
        commit_hash=commit_hash,
        metadata=fallback_metadata,
    )

    logger.info(
        "Local clone manifest built for %s/%s: %d files at %s",
        owner,
        repo,
        manifest.total_files,
        commit_hash[:8],
    )
    return fallback_metadata, commit_hash, manifest, clone_dir


# ─── Orchestrator ─────────────────────────────────────────────────────────────


async def fetch_repository(
    owner: str,
    repo: str,
    github_url: str,
) -> tuple[dict, str, RepoManifest]:
    """
    Orchestrate the full data ingestion pipeline for a single repository.

    Execution order:
      1. Fetch repo metadata (name, branch, stars, language, …)
      2. Fetch the latest commit SHA (fingerprint)
      3. Fetch the file tree and build the RepoManifest
      4. Optionally clone the repo locally (if ENABLE_CLONE=true)

    Args:
        owner:      Repository owner parsed from the GitHub URL.
        repo:       Repository name parsed from the GitHub URL.
        github_url: Full normalised GitHub URL.

    Returns:
        A 3-tuple:
          - metadata    (dict)        — raw GitHub metadata fields
          - commit_hash (str)         — 40-char SHA
          - manifest    (RepoManifest) — structured file tree

    Note: The clone path (if any) is not returned here because it is an
    implementation detail of the parser layer (Step 2), not the API layer.
    """
    logger.info("Starting repository fetch: %s/%s", owner, repo)

    # Step 1: metadata
    metadata = await get_repo_metadata(owner, repo)

    # Step 2: fingerprint
    commit_hash = await get_latest_commit(owner, repo, metadata["default_branch"])

    # Step 3: file manifest
    manifest = await get_file_manifest(owner, repo, commit_hash, metadata)

    logger.info("Repository fetch complete: %s/%s (%s)", owner, repo, commit_hash[:8])
    return metadata, commit_hash, manifest


# ─── File content fetchers (used by the parser layer) ─────────────────────────


async def get_file_content(
    owner: str,
    repo: str,
    path: str,
    ref: str,
) -> str | None:
    """
    Fetch the decoded text content of a single file via the GitHub Contents API.

    Endpoint: GET /repos/{owner}/{repo}/contents/{path}?ref={sha}

    GitHub returns file content as a base64-encoded string for files up to 1 MB.
    Larger files return a download_url instead; we skip those with a warning.

    Args:
        owner: Repository owner.
        repo:  Repository name.
        path:  Repository-relative file path (e.g. "requirements.txt").
        ref:   Commit SHA or branch name to read at (use commit SHA for determinism).

    Returns:
        Decoded UTF-8 file content, or None if:
          - The file does not exist (404)
          - The file is too large (> 1 MB)
          - The content cannot be decoded as UTF-8 (binary file)
    """
    import base64

    async with _make_client() as client:
        url = f"/repos/{owner}/{repo}/contents/{path}"
        logger.debug("Fetching file content: %s/%s/%s@%s", owner, repo, path, ref[:8])
        response = await client.get(url, params={"ref": ref})

        if response.status_code == 404:
            logger.debug("File not found: %s", path)
            return None

        _handle_github_response(response, f"file content for {path}")
        data = response.json()

        # GitHub returns a list when path is a directory — skip it
        if isinstance(data, list):
            logger.debug("Path is a directory, not a file: %s", path)
            return None

        encoding = data.get("encoding", "")
        content_b64 = data.get("content", "")

        if not content_b64:
            download_url = data.get("download_url")
            if download_url:
                logger.warning(
                    "File '%s' is too large for the Contents API (> 1 MB). "
                    "Skipping. Enable cloning (ENABLE_CLONE=true) to analyse large files.",
                    path,
                )
            return None

        if encoding != "base64":
            logger.warning("Unknown encoding '%s' for file '%s'. Skipping.", encoding, path)
            return None

        try:
            # GitHub base64 includes newlines every 60 chars — strip them before decoding
            raw_bytes = base64.b64decode(content_b64.replace("\n", ""))
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            logger.debug("File '%s' is not valid UTF-8 (likely binary). Skipping.", path)
            return None


async def get_file_contents_batch(
    owner: str,
    repo: str,
    paths: list[str],
    ref: str,
    *,
    max_concurrent: int = 10,
) -> dict[str, str]:
    """
    Fetch the decoded content of multiple files concurrently.

    Uses asyncio.gather with a semaphore to bound concurrent requests and
    avoid triggering GitHub's secondary rate limit (which kicks in on
    rapid-fire bursts regardless of the hourly quota).

    Args:
        owner:          Repository owner.
        repo:           Repository name.
        paths:          List of repository-relative file paths to fetch.
        ref:            Commit SHA or branch (use SHA for determinism).
        max_concurrent: Maximum simultaneous in-flight requests.

    Returns:
        Dict mapping {relative_path: decoded_content} for every file that
        was successfully fetched. Missing/binary files are silently omitted.
    """
    import asyncio

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(path: str) -> tuple[str, str | None]:
        async with semaphore:
            try:
                content = await get_file_content(owner, repo, path, ref)
                return path, content
            except GitHubRateLimitError as exc:
                logger.warning(
                    "Rate limit reached while fetching '%s'. "
                    "Continuing parser run with partial file contents: %s",
                    path,
                    exc,
                )
                return path, None
            except GitHubServiceError as exc:
                logger.warning(
                    "Skipping '%s' after GitHub API error. "
                    "Continuing parser run with partial file contents: %s",
                    path,
                    exc,
                )
                return path, None

    logger.info(
        "Batch-fetching %d file(s) from %s/%s@%s (max_concurrent=%d)",
        len(paths), owner, repo, ref[:8], max_concurrent,
    )

    results = await asyncio.gather(*[_fetch_one(p) for p in paths])

    fetched: dict[str, str] = {}
    for path, content in results:
        if content is not None:
            fetched[path] = content

    logger.info(
        "Batch fetch complete: %d/%d files retrieved",
        len(fetched), len(paths),
    )
    return fetched
