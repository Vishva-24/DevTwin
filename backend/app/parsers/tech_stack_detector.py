"""
app/parsers/tech_stack_detector.py

Deterministic, rule-based technology stack detector.

Architecture:
  This module is a pure data processor. It accepts:
    1. A RepoManifest (list of file paths + sizes from the GitHub Trees API)
    2. An optional dict of {relative_path: file_content} for specific
       dependency declaration files (requirements.txt, package.json, etc.)

  It never fetches files itself. The parser orchestrator (parser_service.py)
  is responsible for fetching the content of relevant config files via the
  GitHub Contents API and passing them in here.

Detection Passes:
  Pass 1 — Path Analysis (always runs, manifest only):
    - Language detection from file extensions
    - Package manager detection from config file presence
    - Path-based framework hints (manage.py → Django, next.config.* → Next.js)
    - Infrastructure detection (Dockerfile, .github/workflows, etc.)

  Pass 2 — Content Analysis (runs when file_contents is provided):
    - requirements.txt  → Python framework / DB / AI tool signals
    - pyproject.toml    → Same, using regex (avoids tomllib dependency)
    - package.json      → JS/TS framework / DB / AI tool signals
    - Pipfile           → Python framework / DB / AI tool signals

Design decisions:
  - All rule tables are module-level constants (dicts). Adding support for
    a new framework = adding one entry to one dict.
  - Language is only reported if >= MIN_LANGUAGE_FILES files share that
    extension, preventing single-file false positives.
  - Returns a Pydantic BaseModel for direct serialization into the
    Analysis.tech_stack JSONB column.
  - detection_notes captures WHY each item was detected — useful for
    debugging and for building the Gemini prompt context in Step 3.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas.manifest import FileEntry, RepoManifest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Minimum number of files with a given extension to report that language.
# Prevents single-file false positives (e.g., one .rb file in a Python project).
MIN_LANGUAGE_FILES: int = 2

# ── Detection rule tables ──────────────────────────────────────────────────────
# These are intentionally kept as plain module-level dicts.
# To add a new language/framework/tool: add one line here. No other changes needed.

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    # Systems / compiled
    ".py":    "Python",
    ".go":    "Go",
    ".rs":    "Rust",
    ".java":  "Java",
    ".kt":    "Kotlin",
    ".swift": "Swift",
    ".cs":    "C#",
    ".cpp":   "C++",
    ".cc":    "C++",
    ".c":     "C",
    ".h":     "C/C++",
    ".hpp":   "C++",
    # Web
    ".ts":    "TypeScript",
    ".tsx":   "TypeScript",
    ".js":    "JavaScript",
    ".jsx":   "JavaScript",
    ".mjs":   "JavaScript",
    ".cjs":   "JavaScript",
    # Mobile
    ".dart":  "Dart",
    # Scripting / other
    ".rb":    "Ruby",
    ".php":   "PHP",
    ".scala": "Scala",
    ".r":     "R",
    ".ex":    "Elixir",
    ".exs":   "Elixir",
    ".clj":   "Clojure",
    ".hs":    "Haskell",
    ".lua":   "Lua",
    ".jl":    "Julia",
    # Infrastructure-as-code (treated as a "language" for stack purposes)
    ".tf":    "Terraform/HCL",
    ".sol":   "Solidity",
}

# Presence of these filenames (basename only) indicates a package manager.
# Value is the canonical package manager name.
_PACKAGE_MANAGER_FILES: dict[str, str] = {
    "requirements.txt":   "pip",
    "pyproject.toml":     "pip/poetry/uv",
    "Pipfile":            "pipenv",
    "poetry.lock":        "poetry",
    "uv.lock":            "uv",
    "package.json":       "npm/yarn/pnpm",
    "yarn.lock":          "yarn",
    "pnpm-lock.yaml":     "pnpm",
    "bun.lockb":          "bun",
    "go.mod":             "Go modules",
    "Cargo.toml":         "cargo",
    "Gemfile":            "bundler",
    "composer.json":      "composer",
    "pom.xml":            "maven",
    "build.gradle":       "gradle",
    "build.gradle.kts":   "gradle",
    "mix.exs":            "mix",
}

# Python package name → framework label.
# Keys are lowercase pip package names (stripped of version specifiers).
_PYTHON_FRAMEWORK_SIGNALS: dict[str, str] = {
    "fastapi":      "FastAPI",
    "django":       "Django",
    "flask":        "Flask",
    "starlette":    "Starlette",
    "tornado":      "Tornado",
    "aiohttp":      "aiohttp",
    "sanic":        "Sanic",
    "litestar":     "Litestar",
    "falcon":       "Falcon",
    "bottle":       "Bottle",
    "pyramid":      "Pyramid",
    "celery":       "Celery",
    "dramatiq":     "Dramatiq",
    "pydantic":     "Pydantic",
    "sqlalchemy":   "SQLAlchemy",
    "alembic":      "Alembic",
    "pytest":       "pytest",
    "httpx":        "httpx",
    "uvicorn":      "Uvicorn",
    "gunicorn":     "Gunicorn",
    "grpcio":       "gRPC",
    "graphene":     "Graphene (GraphQL)",
    "strawberry-graphql": "Strawberry (GraphQL)",
}

# npm/yarn package name → framework label.
_JS_FRAMEWORK_SIGNALS: dict[str, str] = {
    "next":               "Next.js",
    "react":              "React",
    "react-dom":          "React",
    "vue":                "Vue.js",
    "@angular/core":      "Angular",
    "express":            "Express.js",
    "@nestjs/core":       "NestJS",
    "svelte":             "Svelte",
    "@sveltejs/kit":      "SvelteKit",
    "nuxt":               "Nuxt.js",
    "remix":              "Remix",
    "astro":              "Astro",
    "gatsby":             "Gatsby",
    "vite":               "Vite",
    "webpack":            "webpack",
    "electron":           "Electron",
    "fastify":            "Fastify",
    "koa":                "Koa",
    "hapi":               "@hapi",
    "socket.io":          "Socket.IO",
    "graphql":            "GraphQL",
    "@apollo/server":     "Apollo Server",
    "@apollo/client":     "Apollo Client",
    "prisma":             "Prisma",
    "drizzle-orm":        "Drizzle ORM",
    "typeorm":            "TypeORM",
    "sequelize":          "Sequelize",
    "mongoose":           "Mongoose",
    "trpc":               "tRPC",
    "@trpc/server":       "tRPC",
}

# Package name signals → database label.
# Checked in BOTH Python and JS dependency files.
_DB_SIGNALS: dict[str, str] = {
    "psycopg2":           "PostgreSQL",
    "psycopg2-binary":    "PostgreSQL",
    "psycopg":            "PostgreSQL",
    "psycopg-binary":     "PostgreSQL",
    "asyncpg":            "PostgreSQL",
    "pg":                 "PostgreSQL",
    "postgres":           "PostgreSQL",
    "pymongo":            "MongoDB",
    "motor":              "MongoDB",
    "mongoose":           "MongoDB",
    "redis":              "Redis",
    "aioredis":           "Redis",
    "ioredis":            "Redis",
    "upstash-redis":      "Redis (Upstash)",
    "mysql":              "MySQL",
    "pymysql":            "MySQL",
    "mysql2":             "MySQL",
    "aiomysql":           "MySQL",
    "sqlite3":            "SQLite",
    "better-sqlite3":     "SQLite",
    "elasticsearch":      "Elasticsearch",
    "opensearch-py":      "OpenSearch",
    "cassandra-driver":   "Cassandra",
    "supabase":           "Supabase",
    "firebase-admin":     "Firebase",
    "dynamodb":           "DynamoDB",
    "boto3":              "AWS SDK",
    "aiobotocore":        "AWS SDK",
    "@aws-sdk":           "AWS SDK",
    "neon":               "Neon (PostgreSQL)",
    "libsql-client":      "Turso (SQLite)",
    "planetscale":        "PlanetScale",
}

# Package name signals → AI/ML tool label.
_AI_SIGNALS: dict[str, str] = {
    "openai":                   "OpenAI",
    "anthropic":                "Anthropic/Claude",
    "google-generativeai":      "Google Gemini",
    "@google/generative-ai":    "Google Gemini",
    "langchain":                "LangChain",
    "langchain-core":           "LangChain",
    "langchain-community":      "LangChain",
    "langgraph":                "LangGraph",
    "llama-index":              "LlamaIndex",
    "llama_index":              "LlamaIndex",
    "transformers":             "HuggingFace Transformers",
    "huggingface-hub":          "HuggingFace Hub",
    "datasets":                 "HuggingFace Datasets",
    "torch":                    "PyTorch",
    "torchvision":              "PyTorch",
    "tensorflow":               "TensorFlow",
    "keras":                    "Keras",
    "scikit-learn":             "scikit-learn",
    "xgboost":                  "XGBoost",
    "lightgbm":                 "LightGBM",
    "cohere":                   "Cohere",
    "mistralai":                "Mistral AI",
    "groq":                     "Groq",
    "together":                 "Together AI",
    "replicate":                "Replicate",
    "chromadb":                 "ChromaDB",
    "pinecone-client":          "Pinecone",
    "pinecone":                 "Pinecone",
    "weaviate-client":          "Weaviate",
    "qdrant-client":            "Qdrant",
    "pymilvus":                 "Milvus",
    "faiss-cpu":                "FAISS",
    "faiss-gpu":                "FAISS",
    "sentence-transformers":    "Sentence Transformers",
    "tiktoken":                 "tiktoken",
    "instructor":               "Instructor",
    "dspy-ai":                  "DSPy",
    "haystack-ai":              "Haystack",
    "numpy":                    "NumPy",
    "pandas":                   "pandas",
    "matplotlib":               "Matplotlib",
    "scipy":                    "SciPy",
}

# Substring of a lowercased file path → infra tool label.
# Applied to ALL file paths in the manifest.
_INFRA_PATH_SIGNALS: dict[str, str] = {
    "docker-compose":       "Docker Compose",
    "dockerfile":           "Docker",
    ".github/workflows":    "GitHub Actions",
    "kubernetes":           "Kubernetes",
    "/k8s/":                "Kubernetes",
    "helm":                 "Helm",
    ".terraform":           "Terraform",
    "/terraform/":          "Terraform",
    "pulumi":               "Pulumi",
    "serverless.yml":       "Serverless Framework",
    "vercel.json":          "Vercel",
    "netlify.toml":         "Netlify",
    "fly.toml":             "Fly.io",
    "render.yaml":          "Render",
    ".circleci":            "CircleCI",
    "jenkins":              "Jenkins",
    "azure-pipelines":      "Azure Pipelines",
}


# ── Output schema ──────────────────────────────────────────────────────────────


class TechStackResult(BaseModel):
    """
    Structured output of the TechStackDetector.

    Serializes directly into the Analysis.tech_stack JSONB column.
    All list fields are sorted alphabetically for deterministic output.
    """

    languages: list[str] = Field(
        default_factory=list,
        description="Programming languages detected from file extensions.",
    )
    frameworks: list[str] = Field(
        default_factory=list,
        description="Web frameworks, ORMs, and runtimes detected from dependencies.",
    )
    databases: list[str] = Field(
        default_factory=list,
        description="Database systems and ORMs detected from dependency files.",
    )
    ai_tools: list[str] = Field(
        default_factory=list,
        description="AI/ML libraries and platforms detected from dependency files.",
    )
    package_managers: list[str] = Field(
        default_factory=list,
        description="Package managers inferred from lock/config file presence.",
    )
    infra: list[str] = Field(
        default_factory=list,
        description="Infrastructure and DevOps tooling detected from file paths.",
    )
    detection_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable explanation of why each item was detected. "
            "Used for debugging and for building the Gemini summarizer context."
        ),
    )


# ── Detector ───────────────────────────────────────────────────────────────────


class TechStackDetector:
    """
    Stateless, deterministic tech stack detector.

    Usage:
        detector = TechStackDetector()
        result = detector.detect(manifest, file_contents={"requirements.txt": "..."})

    The detector is intentionally stateless (no __init__ state) so it can be
    safely shared across async request handlers.
    """

    def detect(
        self,
        manifest: RepoManifest,
        file_contents: dict[str, str] | None = None,
    ) -> TechStackResult:
        """
        Run the full two-pass tech stack detection pipeline.

        Args:
            manifest:      The RepoManifest from the GitHub fetching service.
                           Provides the full recursive file path list.
            file_contents: Optional mapping of {relative_path: text_content}
                           for dependency declaration files. The caller
                           (parser_service.py) is responsible for fetching
                           these via the GitHub Contents API.

        Returns:
            A TechStackResult Pydantic model ready for JSONB serialization.
        """
        file_contents = file_contents or {}
        files: list[FileEntry] = manifest.files
        all_paths: set[str] = {entry.path for entry in files}
        all_basenames: set[str] = {Path(p).name for p in all_paths}

        notes: list[str] = []

        # ── Pass 1: Path analysis ──────────────────────────────────────────────
        languages = self._detect_languages(files, notes)
        package_managers = self._detect_package_managers(all_basenames, notes)
        path_frameworks = self._detect_frameworks_from_paths(all_paths, all_basenames, notes)
        infra = self._detect_infra(all_paths, notes)

        # ── Pass 2: Dependency file content analysis ───────────────────────────
        content_frameworks: set[str] = set()
        databases: set[str] = set()
        ai_tools: set[str] = set()

        for file_path, content in file_contents.items():
            if not content or not content.strip():
                continue

            basename = Path(file_path).name.lower()
            f, d, a, n = self._dispatch_content_parser(basename, content, file_path)
            content_frameworks |= f
            databases |= d
            ai_tools |= a
            notes.extend(n)

        frameworks = path_frameworks | content_frameworks

        logger.info(
            "TechStack detected for %s/%s: %d languages, %d frameworks, "
            "%d databases, %d AI tools",
            manifest.owner,
            manifest.repo_name,
            len(languages),
            len(frameworks),
            len(databases),
            len(ai_tools),
        )

        return TechStackResult(
            languages=sorted(languages),
            frameworks=sorted(frameworks),
            databases=sorted(databases),
            ai_tools=sorted(ai_tools),
            package_managers=sorted(package_managers),
            infra=sorted(infra),
            detection_notes=notes,
        )

    # ── Pass 1 helpers ─────────────────────────────────────────────────────────

    def _detect_languages(
        self,
        files: list[FileEntry],
        notes: list[str],
    ) -> set[str]:
        """
        Detect programming languages from file extensions.

        Only languages with >= MIN_LANGUAGE_FILES files are included.
        This prevents false positives from incidental files (e.g., one .rb
        file in a predominantly Python project).
        """
        extension_counts: dict[str, int] = {}
        for entry in files:
            ext = Path(entry.path).suffix.lower()
            if ext in _EXTENSION_TO_LANGUAGE:
                extension_counts[ext] = extension_counts.get(ext, 0) + 1

        detected: set[str] = set()
        for ext, count in extension_counts.items():
            lang = _EXTENSION_TO_LANGUAGE[ext]
            if count >= MIN_LANGUAGE_FILES:
                detected.add(lang)
                notes.append(
                    f"Language '{lang}' detected: {count} file(s) with extension '{ext}'"
                )
            else:
                logger.debug(
                    "Skipping language '%s': only %d file(s) with extension '%s' "
                    "(threshold: %d)",
                    lang, count, ext, MIN_LANGUAGE_FILES,
                )
        return detected

    def _detect_package_managers(
        self,
        basenames: set[str],
        notes: list[str],
    ) -> set[str]:
        """Detect package managers from the presence of well-known config files."""
        detected: set[str] = set()
        for filename, pm_name in _PACKAGE_MANAGER_FILES.items():
            if filename in basenames:
                detected.add(pm_name)
                notes.append(
                    f"Package manager '{pm_name}' detected: '{filename}' found in repository"
                )
        return detected

    def _detect_frameworks_from_paths(
        self,
        all_paths: set[str],
        basenames: set[str],
        notes: list[str],
    ) -> set[str]:
        """
        Detect frameworks that leave distinctive file/directory signatures.

        These rules run even WITHOUT dependency file contents, giving a
        baseline that works on any manifest.
        """
        detected: set[str] = set()

        # Django: always generates manage.py at the repo root
        if "manage.py" in basenames:
            detected.add("Django")
            notes.append("Framework 'Django' detected: 'manage.py' found at root level")

        # Next.js: has a next.config.js / next.config.ts / next.config.mjs
        next_configs = {p for p in all_paths if Path(p).stem == "next.config"}
        if next_configs:
            detected.add("Next.js")
            notes.append(
                f"Framework 'Next.js' detected: config file found "
                f"({next(iter(next_configs))})"
            )

        # Alembic: has alembic.ini
        if "alembic.ini" in basenames:
            detected.add("Alembic")
            notes.append("Tool 'Alembic' detected: 'alembic.ini' found in repository")

        # FastAPI: has typical routers/ directory structure
        has_routers_dir = any("routers/" in p or "/routers/" in p for p in all_paths)
        if has_routers_dir and any(
            p.endswith(".py") for p in all_paths
        ):
            notes.append(
                "Path hint: 'routers/' directory found in a Python project "
                "(possible FastAPI/Django/Flask pattern)"
            )

        # Flutter / Dart: has pubspec.yaml
        if "pubspec.yaml" in basenames:
            detected.add("Flutter")
            notes.append("Framework 'Flutter' detected: 'pubspec.yaml' found")

        return detected

    def _detect_infra(
        self,
        all_paths: set[str],
        notes: list[str],
    ) -> set[str]:
        """Detect infrastructure tooling from file path substrings."""
        detected: set[str] = set()
        paths_lower = {p.lower() for p in all_paths}

        for signal, name in _INFRA_PATH_SIGNALS.items():
            if any(signal in p for p in paths_lower):
                detected.add(name)
                notes.append(f"Infra tool '{name}' detected: path contains '{signal}'")

        return detected

    # ── Pass 2 dispatch ────────────────────────────────────────────────────────

    def _dispatch_content_parser(
        self,
        basename: str,
        content: str,
        file_path: str,
    ) -> tuple[set[str], set[str], set[str], list[str]]:
        """
        Route a dependency file to the correct content parser.

        Returns:
            (frameworks, databases, ai_tools, notes)
        """
        if basename == "requirements.txt":
            return self._parse_requirements_txt(content, file_path)
        if basename == "pyproject.toml":
            return self._parse_pyproject_toml(content, file_path)
        if basename == "package.json":
            return self._parse_package_json(content, file_path)
        if basename in ("pipfile", "pipfile.lock"):
            return self._parse_pipfile(content, file_path)

        logger.debug("No content parser registered for '%s' — skipping", basename)
        return set(), set(), set(), []

    # ── Content parsers ────────────────────────────────────────────────────────

    def _parse_requirements_txt(
        self,
        content: str,
        file_path: str,
    ) -> tuple[set[str], set[str], set[str], list[str]]:
        """
        Parse a requirements.txt file.

        Handles:
          - Plain names:                fastapi
          - Version pinned:             fastapi==0.115.5
          - Version ranged:             fastapi>=0.100,<1.0
          - Extras:                     uvicorn[standard]==0.32.1
          - Environment markers:        pydantic; python_version >= "3.8"
          - Comment lines (#) and blank lines are skipped.
          - -r includes and -c constraints are skipped gracefully.
        """
        frameworks: set[str] = set()
        databases: set[str] = set()
        ai_tools: set[str] = set()
        notes: list[str] = []

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue

            # Strip extras [standard], version specifiers, and env markers
            # e.g. "uvicorn[standard]>=0.32; python_version>='3.8'" → "uvicorn"
            pkg = re.split(r"[>=<!;\[\s]", line)[0].strip().lower()
            if not pkg:
                continue

            self._classify_package(
                pkg, "Python", file_path,
                frameworks, databases, ai_tools, notes,
            )

        return frameworks, databases, ai_tools, notes

    def _parse_pyproject_toml(
        self,
        content: str,
        file_path: str,
    ) -> tuple[set[str], set[str], set[str], list[str]]:
        """
        Parse a pyproject.toml file using regex (no tomllib required).

        Handles both PEP 621 (project.dependencies) and Poetry formats.
        We use a conservative regex that matches quoted and unquoted
        dependency entries in standard TOML dependency arrays.

        Pattern matches lines like:
          "fastapi>=0.100"
          'django = ">=4.0"'
          httpx = {version = ">=0.24", extras = ["http2"]}
        """
        frameworks: set[str] = set()
        databases: set[str] = set()
        ai_tools: set[str] = set()
        notes: list[str] = []

        # Match package names from dependency declarations.
        # Covers:  "fastapi>=..."  and  fastapi = ...  forms.
        dep_pattern = re.compile(
            r"""
            (?:                         # two forms:
              "([A-Za-z0-9][A-Za-z0-9._-]*)[>=<!;\[]  # "pkg>=..." in array
              |
              ^[ \t]*([A-Za-z0-9][A-Za-z0-9._-]*)    # pkg = ... at line start
              [ \t]*=                                  #   followed by =
            )
            """,
            re.VERBOSE | re.MULTILINE,
        )

        for match in dep_pattern.finditer(content):
            raw_pkg = (match.group(1) or match.group(2) or "").strip().lower()
            if not raw_pkg or raw_pkg in ("python", "python-requires", "version"):
                continue
            # Normalise: hyphens and underscores are equivalent in pip
            pkg = raw_pkg.replace("_", "-")
            self._classify_package(
                pkg, "Python", file_path,
                frameworks, databases, ai_tools, notes,
            )

        return frameworks, databases, ai_tools, notes

    def _parse_package_json(
        self,
        content: str,
        file_path: str,
    ) -> tuple[set[str], set[str], set[str], list[str]]:
        """
        Parse a package.json file.

        Reads both 'dependencies' and 'devDependencies'.
        Scoped packages (e.g., @nestjs/core) are matched against both the
        full scoped name and the scope prefix.
        """
        frameworks: set[str] = set()
        databases: set[str] = set()
        ai_tools: set[str] = set()
        notes: list[str] = []

        try:
            data: dict = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse JSON in '%s': %s", file_path, exc)
            return frameworks, databases, ai_tools, notes

        all_deps: dict[str, str] = {}
        all_deps.update(data.get("dependencies", {}) or {})
        all_deps.update(data.get("devDependencies", {}) or {})

        for pkg in all_deps:
            pkg_lower = pkg.lower()
            self._classify_package(
                pkg_lower, "JavaScript/TypeScript", file_path,
                frameworks, databases, ai_tools, notes,
                js_mode=True,
            )

        return frameworks, databases, ai_tools, notes

    def _parse_pipfile(
        self,
        content: str,
        file_path: str,
    ) -> tuple[set[str], set[str], set[str], list[str]]:
        """
        Parse a Pipfile (TOML-like format used by pipenv).

        Extracts package names from lines like:
          fastapi = "*"
          requests = ">=2.28"
          django = {version = ">=4.0", extras = ["argon2"]}
        """
        frameworks: set[str] = set()
        databases: set[str] = set()
        ai_tools: set[str] = set()
        notes: list[str] = []

        pkg_pattern = re.compile(
            r'^[ \t]*"?([A-Za-z0-9][A-Za-z0-9._-]*)"?[ \t]*=',
            re.MULTILINE,
        )
        for match in pkg_pattern.finditer(content):
            raw_pkg = match.group(1).strip().lower()
            if raw_pkg in ("python", "python_requires", "source", "url", "verify_ssl", "name"):
                continue
            pkg = raw_pkg.replace("_", "-")
            self._classify_package(
                pkg, "Python", file_path,
                frameworks, databases, ai_tools, notes,
            )

        return frameworks, databases, ai_tools, notes

    # ── Package classifier (shared logic) ──────────────────────────────────────

    def _classify_package(
        self,
        pkg: str,
        ecosystem: str,
        file_path: str,
        frameworks: set[str],
        databases: set[str],
        ai_tools: set[str],
        notes: list[str],
        *,
        js_mode: bool = False,
    ) -> None:
        """
        Test a single normalised package name against all signal tables.

        Mutates the provided sets in-place. Uses prefix matching to handle
        namespace packages (e.g., 'langchain-community' matches 'langchain').

        Args:
            pkg:       Normalised (lowercase, hyphenated) package name.
            ecosystem: Human label used in detection notes ('Python' / 'JS').
            file_path: Source file path, used in notes.
            js_mode:   If True, also checks _JS_FRAMEWORK_SIGNALS in addition
                       to the shared DB and AI signal tables.
        """
        def _matches(signal: str) -> bool:
            """True if pkg equals signal or is a sub-package of it."""
            return pkg == signal or pkg.startswith(signal + "-") or pkg.startswith(signal + "/")

        # Frameworks
        fw_table = _JS_FRAMEWORK_SIGNALS if js_mode else _PYTHON_FRAMEWORK_SIGNALS
        for signal, name in fw_table.items():
            if _matches(signal):
                if name not in frameworks:
                    notes.append(
                        f"Framework '{name}' detected in '{file_path}' "
                        f"[{ecosystem}] (package: {pkg})"
                    )
                frameworks.add(name)
                break

        # Databases
        for signal, name in _DB_SIGNALS.items():
            if _matches(signal):
                databases.add(name)
                break

        # AI tools
        for signal, name in _AI_SIGNALS.items():
            if _matches(signal):
                ai_tools.add(name)
                break


# ── Module-level singleton ─────────────────────────────────────────────────────
# Shared instance for use by the parser orchestrator.
# Safe to share because TechStackDetector is fully stateless.
tech_stack_detector = TechStackDetector()
