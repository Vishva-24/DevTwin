"""
app/parsers/architecture_service.py

Heuristic architecture pattern detector.

Determines the high-level structural pattern of a repository using only the
file and directory paths from the manifest — no file content is required.

Supported patterns (V1):
  - Layered (N-Tier)      : routers/ + services/ + models/ (or repositories/)
  - Hexagonal / Clean     : domain/ + adapters/ + ports/ (or usecases/)
  - MVC                   : controllers/ + views/ + models/
  - Modular Monolith      : multiple feature modules, each self-contained
  - Next.js App           : app/ + components/ (Next.js App Router structure)
  - Frontend SPA          : src/components/ + src/pages/ or src/views/
  - Flat Script / Library : few directories, minimal structure
  - Unknown               : insufficient evidence for any pattern

Algorithm:
  Each pattern has a set of positive signals (directory names or file
  name patterns) with associated integer weights. We tally evidence scores
  across all signals and pick the highest-scoring pattern. If the top two
  patterns are within AMBIGUITY_THRESHOLD of each other, we report both
  as a "Hybrid" architecture.

Design decisions:
  - Zero API calls — runs purely on manifest file paths.
  - Evidence is reported per-signal so the Gemini summarizer can cite
    concrete proof ("routers/, services/, and models/ found") rather than
    asserting a pattern without justification.
  - Patterns are extensible: add a new _ArchPattern dataclass and append
    it to _PATTERNS. No other changes needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas.manifest import RepoManifest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# If the top-2 patterns are within this many points, report as "Hybrid".
AMBIGUITY_THRESHOLD: int = 2

# Minimum score to declare a pattern (avoids spurious detections).
MIN_CONFIDENCE_SCORE: int = 3


# ── Pattern definitions ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _DirSignal:
    """A directory name that contributes to an architecture pattern."""
    name: str       # directory name (case-insensitive match)
    weight: int     # contribution to the pattern score


@dataclass(frozen=True)
class _FileSignal:
    """A filename suffix/basename that contributes to an architecture pattern."""
    name: str       # basename or suffix (case-insensitive)
    weight: int     # contribution to the pattern score
    min_count: int = 1  # minimum files needed before weight is applied


@dataclass
class _ArchPattern:
    """
    A named architectural pattern with its evidence signals and description.
    """
    name: str
    description: str
    dir_signals: list[_DirSignal] = field(default_factory=list)
    file_signals: list[_FileSignal] = field(default_factory=list)


_PATTERNS: list[_ArchPattern] = [

    _ArchPattern(
        name="Layered (N-Tier)",
        description=(
            "Classic separation into distinct horizontal layers: "
            "presentation (routers/api), business logic (services), "
            "and data access (repositories/models). "
            "Common in FastAPI, Django, and Spring Boot projects."
        ),
        dir_signals=[
            _DirSignal("routers",      weight=3),
            _DirSignal("router",       weight=3),
            _DirSignal("routes",       weight=2),
            _DirSignal("services",     weight=3),
            _DirSignal("service",      weight=2),
            _DirSignal("repositories", weight=3),
            _DirSignal("repository",   weight=2),
            _DirSignal("models",       weight=2),
            _DirSignal("model",        weight=1),
            _DirSignal("schemas",      weight=2),
            _DirSignal("schema",       weight=1),
            _DirSignal("core",         weight=1),
            _DirSignal("dependencies", weight=1),
            _DirSignal("deps",         weight=1),
        ],
        file_signals=[
            _FileSignal("router.py",    weight=2),
            _FileSignal("service.py",   weight=2),
            _FileSignal("repository.py",weight=2),
            _FileSignal("models.py",    weight=1),
            _FileSignal("schemas.py",   weight=1),
        ],
    ),

    _ArchPattern(
        name="Hexagonal (Clean Architecture)",
        description=(
            "Ports-and-adapters / Clean Architecture: business logic in the "
            "domain layer, external concerns isolated in adapters. "
            "Strong separation prevents infrastructure from leaking into domain."
        ),
        dir_signals=[
            _DirSignal("domain",      weight=4),
            _DirSignal("adapters",    weight=4),
            _DirSignal("ports",       weight=4),
            _DirSignal("usecases",    weight=3),
            _DirSignal("use_cases",   weight=3),
            _DirSignal("application", weight=2),
            _DirSignal("infrastructure", weight=3),
            _DirSignal("interfaces",  weight=2),
            _DirSignal("entities",    weight=2),
        ],
        file_signals=[
            _FileSignal("use_case.py",   weight=2),
            _FileSignal("port.py",       weight=2),
            _FileSignal("adapter.py",    weight=2),
            _FileSignal("entity.py",     weight=2),
        ],
    ),

    _ArchPattern(
        name="MVC (Model-View-Controller)",
        description=(
            "Model-View-Controller: data models, presentation templates/views, "
            "and controller/handler logic are in separate layers. "
            "Common in Django (with templates), Ruby on Rails, Laravel."
        ),
        dir_signals=[
            _DirSignal("controllers", weight=4),
            _DirSignal("controller",  weight=3),
            _DirSignal("views",       weight=3),
            _DirSignal("view",        weight=2),
            _DirSignal("templates",   weight=3),
            _DirSignal("models",      weight=2),
            _DirSignal("model",       weight=1),
        ],
        file_signals=[
            _FileSignal("views.py",       weight=2),
            _FileSignal("urls.py",        weight=3),   # Django-specific
            _FileSignal("controller.py",  weight=2),
        ],
    ),

    _ArchPattern(
        name="Modular Monolith",
        description=(
            "Feature-based modular structure: each domain feature (users, products, "
            "orders) is a self-contained directory with its own router, service, "
            "and models. Promotes high cohesion and low coupling within a monolith."
        ),
        dir_signals=[
            # These are generic signals; modular detection relies more on
            # counting how many feature-like directories exist.
            _DirSignal("users",    weight=1),
            _DirSignal("products", weight=1),
            _DirSignal("orders",   weight=1),
            _DirSignal("auth",     weight=1),
            _DirSignal("payments", weight=1),
            _DirSignal("billing",  weight=1),
            _DirSignal("admin",    weight=1),
            _DirSignal("accounts", weight=1),
            _DirSignal("profiles", weight=1),
            _DirSignal("items",    weight=1),
            _DirSignal("posts",    weight=1),
            _DirSignal("comments", weight=1),
        ],
        file_signals=[
            # If a module has its own router + service co-located
            _FileSignal("router.py",  weight=1, min_count=2),
            _FileSignal("service.py", weight=1, min_count=2),
            _FileSignal("models.py",  weight=1, min_count=2),
        ],
    ),

    _ArchPattern(
        name="Next.js Application",
        description=(
            "Next.js full-stack web application. May use App Router (app/), "
            "Pages Router (pages/), or both. Often paired with API routes, "
            "React components, and TypeScript."
        ),
        dir_signals=[
            _DirSignal("app",        weight=3),   # App Router
            _DirSignal("pages",      weight=3),   # Pages Router
            _DirSignal("components", weight=2),
            _DirSignal("public",     weight=2),
            _DirSignal("styles",     weight=1),
            _DirSignal("hooks",      weight=2),
            _DirSignal("lib",        weight=1),
            _DirSignal("context",    weight=1),
            _DirSignal("store",      weight=1),
        ],
        file_signals=[
            _FileSignal("next.config.js",  weight=5),
            _FileSignal("next.config.ts",  weight=5),
            _FileSignal("next.config.mjs", weight=5),
            _FileSignal("layout.tsx",      weight=3),
            _FileSignal("layout.ts",       weight=3),
            _FileSignal("page.tsx",        weight=3, min_count=2),
            _FileSignal("page.ts",         weight=3, min_count=2),
            _FileSignal("route.ts",        weight=2, min_count=2),
            _FileSignal("route.tsx",       weight=2, min_count=2),
        ],
    ),

    _ArchPattern(
        name="Frontend SPA",
        description=(
            "Single-Page Application without a Next.js framework. "
            "Typically React, Vue, Angular, or Svelte with a bundler (Vite, webpack). "
            "Source lives in src/ with components/, pages/ or views/."
        ),
        dir_signals=[
            _DirSignal("src",        weight=2),
            _DirSignal("components", weight=2),
            _DirSignal("views",      weight=2),
            _DirSignal("pages",      weight=2),
            _DirSignal("assets",     weight=1),
            _DirSignal("store",      weight=1),
            _DirSignal("hooks",      weight=2),
            _DirSignal("utils",      weight=1),
        ],
        file_signals=[
            _FileSignal("vite.config.ts",     weight=3),
            _FileSignal("vite.config.js",     weight=3),
            _FileSignal("webpack.config.js",  weight=3),
            _FileSignal("index.html",         weight=2),
            _FileSignal("app.tsx",            weight=2),
            _FileSignal("app.ts",             weight=2),
            _FileSignal("app.vue",            weight=3),
            _FileSignal("app.svelte",         weight=3),
        ],
    ),

    _ArchPattern(
        name="Flat Script / Library",
        description=(
            "Minimal structure — scripts, utilities, or a Python library "
            "without significant layering. Common for data science notebooks, "
            "CLI tools, small APIs, and open-source packages."
        ),
        dir_signals=[
            # This pattern is detected by absence, not presence,
            # so it has low weights — it wins only when others score very low.
            _DirSignal("src", weight=1),
        ],
        file_signals=[
            _FileSignal("setup.py",     weight=2),
            _FileSignal("setup.cfg",    weight=2),
            _FileSignal("pyproject.toml", weight=1),
            _FileSignal("__main__.py",  weight=2),
            _FileSignal("cli.py",       weight=2),
        ],
    ),
]


# ── Output schema ──────────────────────────────────────────────────────────────

class ArchitectureSignalEvidence(BaseModel):
    """A single piece of evidence that contributed to the detected pattern."""
    signal: str = Field(description="Directory or file name that matched.")
    weight: int = Field(description="Score contribution of this signal.")
    count: int = Field(
        default=1,
        description="Number of occurrences found in the repository.",
    )


class ArchitecturePatternResult(BaseModel):
    """Score and evidence for a single candidate architecture pattern."""
    pattern: str = Field(description="Pattern name.")
    score: int = Field(description="Total evidence score.")
    evidence: list[ArchitectureSignalEvidence] = Field(default_factory=list)


class ArchitectureResult(BaseModel):
    """
    Structured output of the ArchitectureService.

    Serializes directly into the Analysis.architecture_hints JSONB column.
    """
    primary_pattern: str = Field(
        description=(
            "The most likely architectural pattern. "
            "May be 'Hybrid: X + Y' if two patterns score very close."
        ),
    )
    description: str = Field(
        description="Human-readable explanation of the detected pattern.",
    )
    confidence: str = Field(
        description="'high', 'medium', or 'low' based on evidence score.",
    )
    evidence: list[ArchitectureSignalEvidence] = Field(
        default_factory=list,
        description="Concrete file/directory signals that led to this detection.",
    )
    all_scores: list[ArchitecturePatternResult] = Field(
        default_factory=list,
        description="Full ranked list of all patterns and their scores.",
    )
    top_level_directories: list[str] = Field(
        default_factory=list,
        description="Unique top-level directory names found in the repository.",
    )
    detection_notes: list[str] = Field(default_factory=list)


# ── Service ────────────────────────────────────────────────────────────────────

class ArchitectureService:
    """
    Stateless heuristic architecture pattern detector.

    Requires zero GitHub API calls — operates purely on manifest file paths.

    Usage:
        service = ArchitectureService()
        result = service.detect(manifest)  # no file_contents needed
    """

    def detect(self, manifest: RepoManifest) -> ArchitectureResult:
        """
        Detect the architectural pattern from the repository manifest.

        Args:
            manifest: The full RepoManifest from the GitHub fetching service.

        Returns:
            ArchitectureResult with pattern, confidence, and evidence.
        """
        all_paths = {entry.path for entry in manifest.files}

        # Extract unique directory names at all levels
        all_dir_names: dict[str, int] = {}  # dirname → occurrence count
        all_file_basenames: dict[str, int] = {}  # basename → occurrence count
        top_level_dirs: set[str] = set()

        for path in all_paths:
            parts = Path(path).parts
            if len(parts) > 1:
                top_level_dirs.add(parts[0].lower())
            for i, part in enumerate(parts[:-1]):  # exclude filename
                name = part.lower()
                all_dir_names[name] = all_dir_names.get(name, 0) + 1
            basename = parts[-1].lower()
            all_file_basenames[basename] = all_file_basenames.get(basename, 0) + 1

        # ── Score each pattern ─────────────────────────────────────────────────
        pattern_results: list[ArchitecturePatternResult] = []

        for pattern in _PATTERNS:
            score = 0
            evidence: list[ArchitectureSignalEvidence] = []

            for sig in pattern.dir_signals:
                count = all_dir_names.get(sig.name.lower(), 0)
                if count > 0:
                    score += sig.weight
                    evidence.append(ArchitectureSignalEvidence(
                        signal=f"{sig.name}/",
                        weight=sig.weight,
                        count=count,
                    ))

            for sig in pattern.file_signals:
                count = all_file_basenames.get(sig.name.lower(), 0)
                if count >= sig.min_count:
                    score += sig.weight
                    evidence.append(ArchitectureSignalEvidence(
                        signal=sig.name,
                        weight=sig.weight,
                        count=count,
                    ))

            # Special boost for Modular Monolith:
            # It scores higher when MULTIPLE feature directories each have
            # their own router/service/model files (not just one central layer).
            if pattern.name == "Modular Monolith":
                feature_dirs_with_router = self._count_dirs_with_file(
                    all_paths, "router.py"
                )
                feature_dirs_with_service = self._count_dirs_with_file(
                    all_paths, "service.py"
                )
                if feature_dirs_with_router >= 2:
                    boost = feature_dirs_with_router * 2
                    score += boost
                    evidence.append(ArchitectureSignalEvidence(
                        signal="router.py (multiple modules)",
                        weight=boost,
                        count=feature_dirs_with_router,
                    ))
                if feature_dirs_with_service >= 2:
                    boost = feature_dirs_with_service * 2
                    score += boost
                    evidence.append(ArchitectureSignalEvidence(
                        signal="service.py (multiple modules)",
                        weight=boost,
                        count=feature_dirs_with_service,
                    ))

            pattern_results.append(ArchitecturePatternResult(
                pattern=pattern.name,
                score=score,
                evidence=sorted(evidence, key=lambda e: e.weight, reverse=True),
            ))

        # ── Rank and select ────────────────────────────────────────────────────
        ranked = sorted(pattern_results, key=lambda r: r.score, reverse=True)
        top = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None

        notes: list[str] = []

        if top.score < MIN_CONFIDENCE_SCORE:
            primary_pattern = "Unknown"
            description = (
                "Insufficient structural evidence to determine an architecture pattern. "
                "The repository may be very small, use an unconventional layout, "
                "or consist primarily of configuration and documentation files."
            )
            confidence = "low"
            top_evidence = []

        elif (
            runner_up is not None
            and runner_up.score >= MIN_CONFIDENCE_SCORE
            and (top.score - runner_up.score) <= AMBIGUITY_THRESHOLD
        ):
            # Hybrid: two patterns are nearly tied
            primary_pattern = f"Hybrid: {top.pattern} + {runner_up.pattern}"
            description = (
                f"The repository shows strong signals for both '{top.pattern}' "
                f"and '{runner_up.pattern}', suggesting a hybrid approach or "
                f"a codebase in transition between patterns. "
                f"Scores: {top.pattern}={top.score}, {runner_up.pattern}={runner_up.score}."
            )
            confidence = "medium"
            top_evidence = top.evidence + runner_up.evidence
            notes.append(
                f"Hybrid detected: '{top.pattern}' (score={top.score}) vs "
                f"'{runner_up.pattern}' (score={runner_up.score}) — "
                f"within ambiguity threshold of {AMBIGUITY_THRESHOLD}"
            )

        else:
            primary_pattern = top.pattern
            description = self._get_pattern_description(top.pattern)
            confidence = self._score_to_confidence(top.score)
            top_evidence = top.evidence
            notes.append(
                f"Primary pattern '{top.pattern}' detected with score={top.score} "
                f"(runner-up: '{runner_up.pattern if runner_up else 'none'}' "
                f"score={runner_up.score if runner_up else 0})"
            )

        notes.append(
            f"Top-level directories: {sorted(top_level_dirs)}"
        )

        logger.info(
            "Architecture detection for %s/%s: '%s' (confidence=%s, score=%d)",
            manifest.owner,
            manifest.repo_name,
            primary_pattern,
            confidence,
            top.score,
        )

        return ArchitectureResult(
            primary_pattern=primary_pattern,
            description=description,
            confidence=confidence,
            evidence=top_evidence,
            all_scores=ranked,
            top_level_directories=sorted(top_level_dirs),
            detection_notes=notes,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _count_dirs_with_file(all_paths: set[str], filename: str) -> int:
        """
        Count distinct parent directories that contain the given filename.

        Used to detect Modular Monolith: multiple feature directories each
        having their own router.py / service.py, rather than one central one.
        """
        dirs: set[str] = set()
        fn_lower = filename.lower()
        for path in all_paths:
            p = Path(path)
            if p.name.lower() == fn_lower:
                dirs.add(str(p.parent))
        return len(dirs)

    @staticmethod
    def _score_to_confidence(score: int) -> str:
        """Map a raw evidence score to a human-readable confidence level."""
        if score >= 10:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _get_pattern_description(pattern_name: str) -> str:
        """Return the description for a named pattern from _PATTERNS."""
        for p in _PATTERNS:
            if p.name == pattern_name:
                return p.description
        return "Architecture pattern detected via heuristic analysis."


# ── Module-level singleton ─────────────────────────────────────────────────────
architecture_service = ArchitectureService()
