"""
app/parsers/route_service.py

Deterministic API route extractor for FastAPI and Next.js (App Router).

Scope (V1):
  - FastAPI:  Detects @app.<method>() and @router.<method>() decorators.
              Extracts the HTTP method, path, source file, and line number.
              Attempts to resolve APIRouter prefixes from the same file.
  - Next.js:  Detects `export [async] function <METHOD>(` in route.ts/js files
              under the app/ directory. Derives the HTTP path from the file path
              (App Router convention: app/api/users/route.ts → /api/users).

Out of scope (V1):
  - Django URL patterns (urls.py)
  - Express.js app.get() / router.get()
  - Flask @app.route()
  - FastAPI include_router() prefix merging across files
  - GraphQL resolvers

Architecture:
  This service is purely a data processor — it never fetches files.
  The orchestrator calls get_candidate_files(manifest) to learn which
  files are worth fetching, then passes the content back via detect().

Detection strategy:
  - Regex-based, not AST-based. Route patterns are highly regular and
    regex is more resilient to partial/malformed files than ast.parse().
  - The AST module is reserved for the dependency graph (ast_service.py)
    where its structural accuracy is essential.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from app.schemas.manifest import RepoManifest

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"})

# FastAPI route decorator pattern.
# Matches:
#   @app.get("/path")
#   @router.post("/items/{item_id}", response_model=Item)
#   @api_router.delete( "/users/{id}" )
_FASTAPI_ROUTE_RE = re.compile(
    r"@(\w+)\."
    r"(get|post|put|delete|patch|options|head)"
    r"\s*\(\s*"
    r'["\']([^"\']*)["\']',
    re.IGNORECASE,
)

# FastAPI APIRouter instantiation with optional prefix.
# Matches:
#   router = APIRouter(prefix="/users")
#   api_router = APIRouter(prefix='/api/v1', tags=["users"])
_FASTAPI_ROUTER_DEF_RE = re.compile(
    r"(\w+)\s*=\s*APIRouter\s*\([^)]*"
    r'prefix\s*=\s*["\']([^"\']*)["\']',
)

# Next.js App Router: exported HTTP handler functions.
# Matches:
#   export async function GET(request: NextRequest) {
#   export function POST(req: Request) {
#   export async function DELETE(
_NEXTJS_HANDLER_RE = re.compile(
    r"export\s+(?:async\s+)?function\s+"
    r"(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)"
    r"\s*\(",
)

# Files worth scanning for FastAPI routes.
# Root-level entry points + any Python file under these directory patterns.
_FASTAPI_ROOT_CANDIDATES = frozenset(
    {"main.py", "app.py", "server.py", "application.py", "wsgi.py", "asgi.py"}
)
_FASTAPI_DIR_PATTERNS = frozenset(
    {"routers", "router", "routes", "route", "api", "endpoints", "views", "handlers"}
)

# Next.js App Router route files live here.
_NEXTJS_ROUTE_FILENAME = frozenset({"route.ts", "route.js", "route.tsx", "route.jsx"})
# Next.js Pages Router API routes live here.
_NEXTJS_PAGES_API_DIR = "pages/api"


# ── Output schema ──────────────────────────────────────────────────────────────


class RouteEntry(BaseModel):
    """A single detected API route."""

    method: str = Field(description="HTTP method in uppercase: GET, POST, PUT, ...")
    path: str = Field(description="URL path as declared in the route handler.")
    file: str = Field(description="Relative path of the source file.")
    line: int = Field(description="1-indexed line number of the route declaration.")
    framework: str = Field(description="'FastAPI' or 'Next.js'")
    router_variable: str | None = Field(
        default=None,
        description=(
            "FastAPI only. Name of the router variable (e.g. 'router', 'app'). "
            "Useful for understanding the include_router() structure."
        ),
    )
    prefix: str | None = Field(
        default=None,
        description=(
            "FastAPI only. APIRouter prefix detected in the same file "
            "(e.g. '/users'). May be None if the router has no prefix "
            "or the prefix is defined in another file."
        ),
    )


class RouteExtractionResult(BaseModel):
    """Structured output of the RouteService. Serializes into api_routes JSONB."""

    routes: list[RouteEntry] = Field(default_factory=list)
    total_routes: int = Field(default=0)
    files_scanned: int = Field(default=0)
    frameworks_found: list[str] = Field(default_factory=list)
    detection_notes: list[str] = Field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────────────


class RouteService:
    """
    Stateless API route extractor for FastAPI and Next.js.

    Usage:
        service = RouteService()
        candidates = service.get_candidate_files(manifest)
        # Orchestrator fetches content of candidate files...
        result = service.detect(manifest, file_contents)
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_candidate_files(self, manifest: RepoManifest) -> list[str]:
        """
        Return the list of file paths that should be fetched for route extraction.

        Called by the orchestrator BEFORE detect() so it knows which files to
        retrieve from the GitHub Contents API.

        Args:
            manifest: The full RepoManifest from the GitHub service.

        Returns:
            List of relative file paths to fetch (bounded to avoid API waste).
        """
        candidates: list[str] = []
        all_paths = {entry.path for entry in manifest.files}

        for path in all_paths:
            p = Path(path)
            basename = p.name.lower()
            parent_parts = {part.lower() for part in p.parts[:-1]}

            # FastAPI candidates
            if basename.endswith(".py"):
                if basename in _FASTAPI_ROOT_CANDIDATES:
                    candidates.append(path)
                elif parent_parts & _FASTAPI_DIR_PATTERNS:
                    candidates.append(path)

            # Next.js App Router candidates (route.ts / route.js / etc.)
            elif basename in _NEXTJS_ROUTE_FILENAME:
                candidates.append(path)

            # Next.js Pages Router API candidates
            elif basename.endswith((".ts", ".js", ".tsx", ".jsx")):
                if _NEXTJS_PAGES_API_DIR in path.lower():
                    candidates.append(path)

        logger.info(
            "Route candidate files for %s/%s: %d file(s)",
            manifest.owner,
            manifest.repo_name,
            len(candidates),
        )
        return candidates

    def detect(
        self,
        manifest: RepoManifest,
        file_contents: dict[str, str],
    ) -> RouteExtractionResult:
        """
        Extract API routes from the provided file contents.

        Args:
            manifest:      Full RepoManifest (used for logging context).
            file_contents: Mapping of {relative_path: text_content} for the
                           candidate files returned by get_candidate_files().
                           Files with empty content are skipped gracefully.

        Returns:
            RouteExtractionResult with all discovered routes.
        """
        all_routes: list[RouteEntry] = []
        notes: list[str] = []
        frameworks_found: set[str] = set()
        files_scanned = 0

        for file_path, content in file_contents.items():
            if not content or not content.strip():
                logger.debug("Skipping empty file: %s", file_path)
                continue

            files_scanned += 1
            p = Path(file_path)

            if p.suffix.lower() == ".py":
                routes, file_notes = self._extract_fastapi_routes(file_path, content)
                if routes:
                    frameworks_found.add("FastAPI")
                all_routes.extend(routes)
                notes.extend(file_notes)

            elif p.suffix.lower() in (".ts", ".js", ".tsx", ".jsx"):
                routes, file_notes = self._extract_nextjs_routes(file_path, content)
                if routes:
                    frameworks_found.add("Next.js")
                all_routes.extend(routes)
                notes.extend(file_notes)

        # Deduplicate routes with identical (method, path, file) tuples.
        # This handles edge cases where a file is passed twice.
        seen: set[tuple[str, str, str]] = set()
        unique_routes: list[RouteEntry] = []
        for route in all_routes:
            key = (route.method, route.path, route.file)
            if key not in seen:
                seen.add(key)
                unique_routes.append(route)

        logger.info(
            "Route extraction complete for %s/%s: %d routes across %d file(s)",
            manifest.owner,
            manifest.repo_name,
            len(unique_routes),
            files_scanned,
        )

        return RouteExtractionResult(
            routes=unique_routes,
            total_routes=len(unique_routes),
            files_scanned=files_scanned,
            frameworks_found=sorted(frameworks_found),
            detection_notes=notes,
        )

    # ── FastAPI extraction ─────────────────────────────────────────────────────

    def _extract_fastapi_routes(
        self,
        file_path: str,
        content: str,
    ) -> tuple[list[RouteEntry], list[str]]:
        """
        Extract FastAPI route decorators from a Python source file.

        Strategy:
          1. Scan for APIRouter instantiations to build a prefix map:
             {variable_name: prefix_string}
          2. Scan for route decorators line-by-line.
          3. Look up the router variable in the prefix map to attach the
             prefix. If not found (prefix in another file), prefix is None.
        """
        routes: list[RouteEntry] = []
        notes: list[str] = []
        lines = content.splitlines()

        # Step 1: Build prefix map from APIRouter definitions in this file.
        prefix_map: dict[str, str] = {}
        for match in _FASTAPI_ROUTER_DEF_RE.finditer(content):
            var_name = match.group(1)
            prefix = match.group(2)
            prefix_map[var_name] = prefix
            notes.append(
                f"FastAPI APIRouter '{var_name}' with prefix='{prefix}' "
                f"found in '{file_path}'"
            )

        # Step 2: Extract route decorators.
        for line_idx, line in enumerate(lines, start=1):
            match = _FASTAPI_ROUTE_RE.search(line)
            if not match:
                continue

            router_var = match.group(1)
            method = match.group(2).upper()
            path = match.group(3)
            prefix = prefix_map.get(router_var)

            routes.append(
                RouteEntry(
                    method=method,
                    path=path,
                    file=file_path,
                    line=line_idx,
                    framework="FastAPI",
                    router_variable=router_var,
                    prefix=prefix,
                )
            )

        if routes:
            notes.append(
                f"FastAPI: {len(routes)} route(s) detected in '{file_path}'"
            )

        return routes, notes

    # ── Next.js extraction ─────────────────────────────────────────────────────

    def _extract_nextjs_routes(
        self,
        file_path: str,
        content: str,
    ) -> tuple[list[RouteEntry], list[str]]:
        """
        Extract Next.js App Router route handlers from a TypeScript/JavaScript file.

        Path derivation rules:
          app/api/users/route.ts          → /api/users
          app/(auth)/login/route.ts       → /login  (route groups stripped)
          pages/api/users/index.ts        → /api/users
          pages/api/users/[id].ts         → /api/users/{id}

        Exported handler functions (GET, POST, etc.) in the same file all
        share the same derived HTTP path.
        """
        routes: list[RouteEntry] = []
        notes: list[str] = []

        # Derive HTTP path from file path.
        http_path = self._derive_nextjs_path(file_path)

        lines = content.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            match = _NEXTJS_HANDLER_RE.search(line)
            if not match:
                continue

            method = match.group(1).upper()
            routes.append(
                RouteEntry(
                    method=method,
                    path=http_path,
                    file=file_path,
                    line=line_idx,
                    framework="Next.js",
                )
            )

        if routes:
            notes.append(
                f"Next.js: {len(routes)} handler(s) detected in '{file_path}' "
                f"→ HTTP path '{http_path}'"
            )

        return routes, notes

    def _derive_nextjs_path(self, file_path: str) -> str:
        """
        Derive the HTTP URL path from a Next.js file path.

        Handles:
          - App Router:   app/.../route.{ts,js,tsx,jsx}
          - Pages Router: pages/api/.../{filename}.{ts,js}
          - Route groups: (groupName) segments are stripped
          - Dynamic segments: [param] → {param}
          - Catch-all segments: [...param] → {param}

        Examples:
          app/api/users/route.ts             → /api/users
          app/(auth)/dashboard/route.ts      → /dashboard
          app/api/products/[id]/route.ts     → /api/products/{id}
          pages/api/users/index.ts           → /api/users
          pages/api/hello.ts                 → /api/hello
        """
        posix_path = PurePosixPath(file_path)
        parts = list(posix_path.parts)

        # Detect App Router vs Pages Router
        if "app" in parts:
            # Slice from 'app' onward, drop the filename (route.ts)
            start_idx = parts.index("app")
            segments = parts[start_idx + 1 : -1]  # drop 'app' and filename
        elif "pages" in parts:
            # Slice from 'pages' onward, use the filename (minus extension)
            start_idx = parts.index("pages")
            path_parts = parts[start_idx + 1:]
            # Drop extension from last segment; remove 'index' suffix
            last = PurePosixPath(path_parts[-1]).stem if path_parts else ""
            segments = path_parts[:-1] + ([last] if last and last != "index" else [])
        else:
            # Unknown structure — return the full path without extension
            return "/" + str(posix_path.with_suffix(""))

        # Transform each segment:
        transformed: list[str] = []
        for seg in segments:
            # Strip route groups: (groupName) → skip
            if seg.startswith("(") and seg.endswith(")"):
                continue
            # Catch-all: [...param] → {param}
            if seg.startswith("[...") and seg.endswith("]"):
                transformed.append("{" + seg[4:-1] + "}")
            # Dynamic: [param] → {param}
            elif seg.startswith("[") and seg.endswith("]"):
                transformed.append("{" + seg[1:-1] + "}")
            else:
                transformed.append(seg)

        http_path = "/" + "/".join(transformed) if transformed else "/"
        return http_path


# ── Module-level singleton ─────────────────────────────────────────────────────
route_service = RouteService()
