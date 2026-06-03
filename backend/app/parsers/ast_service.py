"""
app/parsers/ast_service.py

File-level Python import dependency graph builder using the standard AST module.

Scope (V1):
  - Detects ONLY: `import module` and `from module import something`
  - Resolves imports to files within the same repository (internal edges)
  - Tracks external (third-party / stdlib) imports separately
  - Handles relative imports (from . import x, from ..core import y)
  - Analyzes Python files only

Out of scope:
  - Function call graphs
  - Class inheritance trees
  - JavaScript / TypeScript imports (no ast.parse equivalent in stdlib)
  - Dynamic imports (__import__, importlib.import_module)

Architecture:
  - get_candidate_files(manifest) → tells orchestrator which files to fetch
  - build_graph(manifest, file_contents) → returns DependencyGraphResult
  - All ast.parse() errors are caught per-file; graph builds from what parses

Output:
  The DependencyGraphResult.nodes and .edges are shaped for direct
  consumption by React Flow on the Next.js frontend:
    nodes: [{id: file_path, label: module_name, ...}]
    edges: [{source: file_path, target: file_path, ...}]

Rate-limit awareness:
  Each file fetch = 1 GitHub Contents API call. Without a token that is
  60 calls/hr. MAX_FILES_TO_ANALYZE caps the analysis to avoid exhausting
  the budget on large repositories.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from app.schemas.manifest import RepoManifest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Maximum Python files sent for AST analysis.
# Each file = 1 GitHub Contents API request.
# Tune upward once a GitHub token is configured.
MAX_FILES_TO_ANALYZE: int = 60

# Directory names whose files are de-prioritised (analysed last, may be cut).
_LOW_PRIORITY_DIRS: frozenset[str] = frozenset({
    "tests", "test", "testing",
    "migrations", "versions",
    "docs", "doc", "examples", "example",
    "scripts", "tools", "utils",  # often standalone helpers
})

# Directory names whose files are HIGH priority (analysed first).
_HIGH_PRIORITY_DIRS: frozenset[str] = frozenset({
    "services", "service",
    "routers", "router", "routes", "route",
    "api", "endpoints",
    "core", "config",
    "models", "model",
    "schemas", "schema",
    "repositories", "repository",
})

# Root-level entry points are always top priority.
_ENTRY_POINT_NAMES: frozenset[str] = frozenset({
    "main.py", "app.py", "server.py",
    "application.py", "wsgi.py", "asgi.py",
})


# ── Internal data structures (not exposed via API) ─────────────────────────────

@dataclass
class _ImportRecord:
    """Raw import extracted from a single AST node."""

    module: str          # e.g. "app.core.config" or "" for bare relative
    names: list[str]     # e.g. ["get_settings"] or ["*"] for import *
    level: int           # 0=absolute, 1='.', 2='..', etc.
    line: int            # 1-indexed source line
    raw_statement: str   # reconstructed for display in detection_notes


# ── Output schema ──────────────────────────────────────────────────────────────

class DependencyNode(BaseModel):
    """
    A single node in the dependency graph.

    The 'id' field is the repository-relative file path, which is both
    unique and stable across re-analyses of the same commit.
    Shaped for React Flow: id + label + metadata.
    """
    id: str = Field(description="Relative file path — unique node identifier.")
    label: str = Field(description="Bare module name (filename without .py).")
    package: str = Field(description="Dotted package path, e.g. 'app.services'.")
    imports_count: int = Field(description="Number of internal files this file imports.")
    imported_by_count: int = Field(description="Number of internal files that import this file.")
    external_imports: list[str] = Field(
        default_factory=list,
        description="External (third-party / stdlib) packages imported by this file.",
    )


class DependencyEdge(BaseModel):
    """
    A directed edge source → target meaning 'source imports from target'.

    Shaped for React Flow: source + target + metadata.
    """
    source: str = Field(description="Relative path of the importing file.")
    target: str = Field(description="Relative path of the imported file.")
    import_statement: str = Field(
        description="Original import statement for display / debugging."
    )
    line: int = Field(description="Line number in the source file.")


class DependencyGraphResult(BaseModel):
    """
    Structured output of the ASTService.

    Serializes directly into the Analysis.dependency_graph JSONB column.
    The nodes/edges arrays are React Flow compatible.
    """
    nodes: list[DependencyNode] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    external_packages: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Mapping of external package name → list of internal files that import it. "
            "Useful for cross-referencing with the tech stack detector output."
        ),
    )
    total_internal_edges: int = Field(default=0)
    files_analyzed: int = Field(default=0)
    files_skipped: int = Field(default=0)
    parse_errors: list[str] = Field(
        default_factory=list,
        description="Files that failed ast.parse() — syntax errors or encoding issues.",
    )
    detection_notes: list[str] = Field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────────────

class ASTService:
    """
    Stateless file-level Python import dependency graph builder.

    Usage (orchestrator pattern):
        service = ASTService()
        candidates = service.get_candidate_files(manifest)
        # orchestrator fetches file_contents for candidates...
        result = service.build_graph(manifest, file_contents)
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_candidate_files(self, manifest: RepoManifest) -> list[str]:
        """
        Return Python files to analyze, sorted by priority and bounded by MAX_FILES.

        Priority order:
          1. Entry points at repo root (main.py, app.py, etc.)
          2. Files in high-priority directories (services/, routers/, core/, etc.)
          3. All other Python files
          4. Files in low-priority directories (tests/, migrations/, docs/)

        __init__.py files are always included (they define package structure)
        but placed at the end of each tier.

        Args:
            manifest: Full RepoManifest.

        Returns:
            Sorted, bounded list of relative file paths.
        """
        all_py_paths = [
            entry.path for entry in manifest.files
            if entry.path.endswith(".py")
        ]

        entry_points: list[str] = []
        high_priority: list[str] = []
        medium_priority: list[str] = []
        low_priority: list[str] = []

        for path in all_py_paths:
            p = Path(path)
            basename = p.name.lower()
            parts_lower = {part.lower() for part in p.parts[:-1]}

            if basename in _ENTRY_POINT_NAMES:
                entry_points.append(path)
            elif parts_lower & _LOW_PRIORITY_DIRS:
                low_priority.append(path)
            elif parts_lower & _HIGH_PRIORITY_DIRS:
                high_priority.append(path)
            else:
                medium_priority.append(path)

        ordered = entry_points + high_priority + medium_priority + low_priority
        candidates = ordered[:MAX_FILES_TO_ANALYZE]

        skipped = len(all_py_paths) - len(candidates)
        if skipped > 0:
            logger.info(
                "ASTService: %d Python files found, analysing top %d "
                "(skipping %d low-priority files). "
                "Add a GITHUB_TOKEN to raise the cap.",
                len(all_py_paths), len(candidates), skipped,
            )
        else:
            logger.info(
                "ASTService: %d Python files found, analysing all.",
                len(all_py_paths),
            )

        return candidates

    def build_graph(
        self,
        manifest: RepoManifest,
        file_contents: dict[str, str],
    ) -> DependencyGraphResult:
        """
        Build a file-level import dependency graph from parsed Python files.

        Args:
            manifest:      Full RepoManifest. Used to know the complete set of
                           internal file paths for import resolution.
            file_contents: Mapping of {relative_path: source_code} for the
                           candidate files. Empty/missing content is skipped.

        Returns:
            DependencyGraphResult with nodes, edges, and external package map.
        """
        # The full set of internal Python paths is the universe for resolution.
        all_internal_paths: set[str] = {
            entry.path for entry in manifest.files
            if entry.path.endswith(".py")
        }

        # Per-file import records: {file_path: [_ImportRecord, ...]}
        file_imports: dict[str, list[_ImportRecord]] = {}
        parse_errors: list[str] = []
        notes: list[str] = []
        files_analyzed = 0
        files_skipped = 0

        # ── Step 1: Parse all provided files ──────────────────────────────────
        for file_path, content in file_contents.items():
            if not content or not content.strip():
                files_skipped += 1
                continue
            if not file_path.endswith(".py"):
                files_skipped += 1
                continue

            imports, error = self._extract_imports(content, file_path)
            if error:
                parse_errors.append(f"{file_path}: {error}")
                files_skipped += 1
            else:
                file_imports[file_path] = imports
                files_analyzed += 1

        # ── Step 2: Resolve imports to internal edges ──────────────────────────
        # edge_set: set of (source, target, import_statement, line)
        edges: list[DependencyEdge] = []
        edge_seen: set[tuple[str, str]] = set()

        # external_packages: {pkg_name: [file_path, ...]}
        external_packages: dict[str, list[str]] = {}

        # Per-node: track in-degree and out-degree for node metadata
        out_degree: dict[str, int] = {}   # file_path → count of internal targets
        in_degree: dict[str, int] = {}    # file_path → count of internal sources
        node_externals: dict[str, list[str]] = {}  # file_path → external pkgs

        for file_path, records in file_imports.items():
            out_degree.setdefault(file_path, 0)
            in_degree.setdefault(file_path, 0)
            node_externals.setdefault(file_path, [])

            for record in records:
                resolved = self._resolve_to_internal_path(
                    record.module, record.level, file_path, all_internal_paths
                )

                if resolved:
                    # Internal edge
                    in_degree.setdefault(resolved, 0)
                    out_degree[file_path] = out_degree.get(file_path, 0) + 1
                    in_degree[resolved] = in_degree.get(resolved, 0) + 1

                    edge_key = (file_path, resolved)
                    if edge_key not in edge_seen:
                        edge_seen.add(edge_key)
                        edges.append(DependencyEdge(
                            source=file_path,
                            target=resolved,
                            import_statement=record.raw_statement,
                            line=record.line,
                        ))
                else:
                    # External import — track the top-level package name
                    top_pkg = record.module.split(".")[0] if record.module else ""
                    if top_pkg and top_pkg not in ("__future__", ""):
                        external_packages.setdefault(top_pkg, [])
                        if file_path not in external_packages[top_pkg]:
                            external_packages[top_pkg].append(file_path)
                        if top_pkg not in node_externals[file_path]:
                            node_externals[file_path].append(top_pkg)

        # ── Step 3: Build nodes ────────────────────────────────────────────────
        # Include every file that was analyzed OR is a target of an edge.
        node_paths: set[str] = set(file_imports.keys())
        for edge in edges:
            node_paths.add(edge.target)

        nodes: list[DependencyNode] = []
        for path in sorted(node_paths):
            p = Path(path)
            label = p.stem  # e.g. "github_service"
            # dotted package: app/services/github_service.py → app.services
            package_parts = list(p.parts[:-1])  # exclude filename
            package = ".".join(package_parts) if package_parts else ""

            nodes.append(DependencyNode(
                id=path,
                label=label,
                package=package,
                imports_count=out_degree.get(path, 0),
                imported_by_count=in_degree.get(path, 0),
                external_imports=sorted(node_externals.get(path, [])),
            ))

        notes.append(
            f"Dependency graph: {len(nodes)} nodes, {len(edges)} internal edges, "
            f"{len(external_packages)} external packages across "
            f"{files_analyzed} analyzed file(s)."
        )

        if parse_errors:
            notes.append(
                f"{len(parse_errors)} file(s) failed to parse (see parse_errors field)."
            )

        logger.info(
            "AST graph for %s/%s: %d nodes, %d edges, %d parse errors",
            manifest.owner,
            manifest.repo_name,
            len(nodes),
            len(edges),
            len(parse_errors),
        )

        return DependencyGraphResult(
            nodes=nodes,
            edges=edges,
            external_packages=external_packages,
            total_internal_edges=len(edges),
            files_analyzed=files_analyzed,
            files_skipped=files_skipped,
            parse_errors=parse_errors,
            detection_notes=notes,
        )

    # ── AST extraction ─────────────────────────────────────────────────────────

    def _extract_imports(
        self,
        source: str,
        file_path: str,
    ) -> tuple[list[_ImportRecord], str | None]:
        """
        Parse a Python source file with ast.parse() and extract all imports.

        Returns:
            (records, error_message)
            error_message is None on success, a string description on failure.

        Handles:
          import os                         → module="os", level=0
          import os.path                    → module="os.path", level=0
          from pathlib import Path          → module="pathlib", level=0
          from app.core import config       → module="app.core", level=0
          from . import service             → module="", level=1
          from ..core import settings       → module="core", level=2
          from .utils import helper         → module="utils", level=1 (relative)
        """
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as exc:
            return [], f"SyntaxError at line {exc.lineno}: {exc.msg}"
        except Exception as exc:
            return [], f"ParseError: {exc}"

        records: list[_ImportRecord] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # `import os`, `import os.path`, `import a, b`
                for alias in node.names:
                    records.append(_ImportRecord(
                        module=alias.name,
                        names=[alias.asname or alias.name],
                        level=0,
                        line=node.lineno,
                        raw_statement=f"import {alias.name}",
                    ))

            elif isinstance(node, ast.ImportFrom):
                # `from X import Y` or `from . import Y`
                module = node.module or ""
                level = node.level or 0
                names = [alias.name for alias in node.names]

                if level == 0:
                    stmt = f"from {module} import {', '.join(names)}"
                else:
                    dots = "." * level
                    stmt = f"from {dots}{module} import {', '.join(names)}"

                records.append(_ImportRecord(
                    module=module,
                    names=names,
                    level=level,
                    line=node.lineno,
                    raw_statement=stmt,
                ))

        return records, None

    # ── Import resolution ──────────────────────────────────────────────────────

    def _resolve_to_internal_path(
        self,
        module: str,
        level: int,
        importing_file: str,
        known_paths: set[str],
    ) -> str | None:
        """
        Attempt to resolve a module reference to a repository-relative file path.

        Resolution strategy:
          1. Construct candidate paths from the module name:
             - <module_as_path>.py        (e.g. app/core/config.py)
             - <module_as_path>/__init__.py (e.g. app/core/__init__.py)
          2. For relative imports (level > 0), anchor to the importing file's
             package directory first, then walk up `level-1` more directories.
          3. Check each candidate against known_paths from the manifest.

        Returns:
            Matched relative path string, or None if the import is external.
        """
        if level > 0:
            return self._resolve_relative(module, level, importing_file, known_paths)
        return self._resolve_absolute(module, known_paths)

    def _resolve_absolute(
        self,
        module: str,
        known_paths: set[str],
    ) -> str | None:
        """Resolve an absolute import (level=0) to a known internal path."""
        if not module:
            return None

        # Convert dotted module to path: app.core.config → app/core/config
        module_path = module.replace(".", "/")
        candidates = [
            f"{module_path}.py",
            f"{module_path}/__init__.py",
        ]

        for candidate in candidates:
            if candidate in known_paths:
                return candidate

        return None

    def _resolve_relative(
        self,
        module: str,
        level: int,
        importing_file: str,
        known_paths: set[str],
    ) -> str | None:
        """
        Resolve a relative import to a known internal path.

        level=1  (.)   → current package directory
        level=2  (..)  → parent package directory
        level=3  (...) → grandparent package directory

        Examples (importing_file = "app/services/github_service.py"):
          from . import models          → app/services/models.py
          from ..core import config     → app/core/config.py
          from .utils import helper     → app/services/utils.py
        """
        # Start at the directory of the importing file
        import_dir = PurePosixPath(importing_file).parent

        # Walk up (level - 1) additional directories for level >= 2
        for _ in range(level - 1):
            import_dir = import_dir.parent

        # Append the module path (if any)
        if module:
            target_base = import_dir / module.replace(".", "/")
        else:
            target_base = import_dir

        candidates = [
            str(target_base) + ".py",
            str(target_base) + "/__init__.py",
        ]

        for candidate in candidates:
            # Normalise: remove any leading "./"
            normalised = candidate.lstrip("./")
            if normalised in known_paths:
                return normalised
            if candidate in known_paths:
                return candidate

        return None


# ── Module-level singleton ─────────────────────────────────────────────────────
ast_service = ASTService()
