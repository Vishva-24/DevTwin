"""
app/services/llm_service.py

Gemini summarizer - Step 3 of the DevTwin Repository Intelligence Engine.

This service only sends structured parser metadata to Gemini. It never sends
raw repository source code, file contents, or full import statements.
"""

from __future__ import annotations

import json
import logging
import re
import warnings
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_ROUTES_FOR_PROMPT = 80
MAX_GRAPH_NODES_FOR_PROMPT = 60
MAX_GRAPH_EDGES_FOR_PROMPT = 80
MAX_NOTABLE_FILES_FOR_PROMPT = 50
MAX_DETECTION_NOTES_FOR_PROMPT = 30
MAX_PROMPT_METADATA_CHARS = 24_000

SUMMARY_FIELDS = (
    "project_purpose",
    "architecture_summary",
    "key_features",
    "strengths_and_weaknesses",
)


class RepoSummary(BaseModel):
    """Strict structured output expected from Gemini and stored in JSONB."""

    model_config = ConfigDict(extra="forbid")

    project_purpose: str = Field(
        min_length=1,
        description="Concise 2-3 sentence summary of what the application does.",
    )
    architecture_summary: str = Field(
        min_length=1,
        description="Explanation of the system design inferred from parser metadata.",
    )
    key_features: list[str] = Field(
        min_length=3,
        max_length=5,
        description="Three to five main features inferred from routes and stack.",
    )
    strengths_and_weaknesses: str = Field(
        min_length=1,
        description="Brief professional assessment of strengths and weaknesses.",
    )


async def generate_repo_summary(parsed_data: dict) -> dict:
    """
    Generate a repository summary from deterministic parser output.

    Args:
        parsed_data: Aggregated metadata from the ingestion and parser layers.
            Expected keys include file_manifest, tech_stack, api_routes,
            dependency_graph, and architecture_hints. Values must be structured
            metadata only, not source code.

    Returns:
        A dict matching RepoSummary. On Gemini/API/config failures, returns a
        deterministic safe fallback rather than raising into the API endpoint.
    """
    safe_data = _jsonable(parsed_data)
    fallback = _build_fallback_summary(safe_data)

    try:
        settings = get_settings()
        if not _has_real_api_key(settings.gemini_api_key):
            logger.warning("Gemini API key not configured. Using fallback repository summary.")
            return fallback

        prompt = _build_prompt(safe_data)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                import google.generativeai as genai  # type: ignore[import-not-found]
        except ImportError:
            logger.exception(
                "google-generativeai is not installed. "
                "Install requirements.txt to enable Gemini summaries."
            )
            return fallback

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        response = await _generate_with_schema_fallback(model, prompt)
        response_text = _extract_response_text(response)
        summary = _parse_repo_summary(response_text)

        logger.info("Gemini repository summary generated with model '%s'.", settings.gemini_model)
        return summary.model_dump(mode="json")

    except Exception as exc:
        logger.exception("Gemini summarizer failed. Using fallback summary: %s", exc)
        return fallback


async def _generate_with_schema_fallback(model: Any, prompt: str) -> Any:
    """
    Ask Gemini for JSON output.

    google-generativeai versions differ slightly in structured-output support,
    so the first attempt uses response_schema and the second keeps strict JSON
    mode plus prompt-level schema instructions.
    """
    generation_config: dict[str, Any] = {
        "temperature": 0.2,
        "top_p": 0.9,
        "max_output_tokens": 1200,
        "response_mime_type": "application/json",
        "response_schema": _gemini_response_schema(),
    }

    try:
        return await model.generate_content_async(
            prompt,
            generation_config=generation_config,
        )
    except TypeError as exc:
        logger.warning(
            "Gemini SDK rejected response_schema locally; retrying JSON mode only: %s",
            exc,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "schema" not in message and "generation_config" not in message:
            raise
        logger.warning(
            "Gemini API rejected structured schema; retrying JSON mode only: %s",
            exc,
        )

    generation_config.pop("response_schema", None)
    return await model.generate_content_async(
        prompt,
        generation_config=generation_config,
    )


def _gemini_response_schema() -> dict[str, Any]:
    """Minimal JSON schema compatible with Gemini structured outputs."""
    return {
        "type": "object",
        "properties": {
            "project_purpose": {
                "type": "string",
                "description": "Concise 2-3 sentence summary of what the app does.",
            },
            "architecture_summary": {
                "type": "string",
                "description": "System design explanation based only on parser metadata.",
            },
            "key_features": {
                "type": "array",
                "description": "Three to five main features inferred from routes and stack.",
                "items": {"type": "string"},
            },
            "strengths_and_weaknesses": {
                "type": "string",
                "description": "Brief professional assessment.",
            },
        },
        "required": list(SUMMARY_FIELDS),
    }


def _build_prompt(parsed_data: dict[str, Any]) -> str:
    context = _build_prompt_context(parsed_data)
    context_json = json.dumps(context, ensure_ascii=True, sort_keys=True)

    if len(context_json) > MAX_PROMPT_METADATA_CHARS:
        logger.warning(
            "Summarizer context exceeded %d chars; trimming dependency graph details.",
            MAX_PROMPT_METADATA_CHARS,
        )
        context["dependency_graph"] = {
            "nodes_in_graph": context.get("dependency_graph", {}).get("nodes_in_graph", 0),
            "edges_in_graph": context.get("dependency_graph", {}).get("edges_in_graph", 0),
            "external_packages": context.get("dependency_graph", {}).get("external_packages", []),
            "note": "Graph details trimmed to keep the LLM payload compact.",
        }
        context_json = json.dumps(context, ensure_ascii=True, sort_keys=True)

    schema_json = json.dumps(_gemini_response_schema(), ensure_ascii=True, sort_keys=True)

    return f"""
You are a Staff Software Engineer analyzing a codebase for DevTwin.

Use ONLY the parsed repository metadata below. You have not seen the raw source
code, and you must not claim that you inspected source code directly. Do not
invent capabilities that are not supported by the metadata. If evidence is
weak or missing, use careful language such as "appears to" or "likely".

Return a single JSON object only. It must match this schema exactly:
{schema_json}

Field requirements:
- project_purpose: concise 2-3 sentence summary of what the app does.
- architecture_summary: explain the system design from detected stack,
  route metadata, dependency graph shape, and architecture hints.
- key_features: list 3-5 main features inferred from API routes and tech stack.
- strengths_and_weaknesses: brief professional assessment.

Parsed metadata:
{context_json}
""".strip()


def _build_prompt_context(parsed_data: dict[str, Any]) -> dict[str, Any]:
    manifest = _as_dict(parsed_data.get("file_manifest") or parsed_data.get("manifest"))
    tech_stack = _as_dict(parsed_data.get("tech_stack"))
    api_routes = _as_dict(parsed_data.get("api_routes") or parsed_data.get("routes"))
    dependency_graph = _as_dict(parsed_data.get("dependency_graph"))
    architecture = _as_dict(
        parsed_data.get("architecture_hints") or parsed_data.get("architecture")
    )

    return {
        "repository": _summarize_manifest(manifest),
        "tech_stack": _summarize_tech_stack(tech_stack),
        "api_routes": _summarize_routes(api_routes),
        "dependency_graph": _summarize_dependency_graph(dependency_graph),
        "architecture_hints": _summarize_architecture(architecture),
        "parser_metadata": {
            "files_fetched": parsed_data.get("files_fetched"),
            "budget_exhausted": parsed_data.get("budget_exhausted"),
        },
    }


def _summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    files = _as_list(manifest.get("files"))
    paths = [entry.get("path", "") for entry in files if isinstance(entry, dict)]
    paths = [path for path in paths if isinstance(path, str) and path]

    extension_counts: Counter[str] = Counter()
    top_level_counts: Counter[str] = Counter()
    notable_files: list[str] = []

    notable_names = {
        "README.md",
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Dockerfile",
        "docker-compose.yml",
        "alembic.ini",
        "manage.py",
        "next.config.js",
        "next.config.ts",
        "next.config.mjs",
        "vite.config.ts",
        "vite.config.js",
    }

    for path in paths:
        posix_path = PurePosixPath(path)
        if posix_path.suffix:
            extension_counts[posix_path.suffix.lower()] += 1
        if len(posix_path.parts) > 1:
            top_level_counts[posix_path.parts[0]] += 1
        else:
            top_level_counts["(root)"] += 1
        if posix_path.name in notable_names and len(notable_files) < MAX_NOTABLE_FILES_FOR_PROMPT:
            notable_files.append(path)

    return {
        "owner": manifest.get("owner"),
        "repo_name": manifest.get("repo_name"),
        "description": manifest.get("description"),
        "primary_language": manifest.get("primary_language"),
        "stars": manifest.get("stars"),
        "default_branch": manifest.get("default_branch"),
        "commit_hash_prefix": _prefix(manifest.get("commit_hash"), 8),
        "total_files": manifest.get("total_files") or len(paths),
        "top_extensions": extension_counts.most_common(12),
        "top_level_directories": top_level_counts.most_common(16),
        "notable_files": notable_files,
    }


def _summarize_tech_stack(tech_stack: dict[str, Any]) -> dict[str, Any]:
    return {
        "languages": _as_list(tech_stack.get("languages")),
        "frameworks": _as_list(tech_stack.get("frameworks")),
        "databases": _as_list(tech_stack.get("databases")),
        "ai_tools": _as_list(tech_stack.get("ai_tools")),
        "package_managers": _as_list(tech_stack.get("package_managers")),
        "infra": _as_list(tech_stack.get("infra")),
        "detection_notes": _as_list(
            tech_stack.get("detection_notes")
        )[:MAX_DETECTION_NOTES_FOR_PROMPT],
    }


def _summarize_routes(api_routes: dict[str, Any]) -> dict[str, Any]:
    routes = []
    for route in _as_list(api_routes.get("routes"))[:MAX_ROUTES_FOR_PROMPT]:
        if not isinstance(route, dict):
            continue
        routes.append(
            {
                "method": route.get("method"),
                "path": route.get("path"),
                "framework": route.get("framework"),
                "file": route.get("file"),
                "prefix": route.get("prefix"),
            }
        )

    return {
        "total_routes": api_routes.get("total_routes") or len(routes),
        "files_scanned": api_routes.get("files_scanned"),
        "frameworks_found": _as_list(api_routes.get("frameworks_found")),
        "routes_sample": routes,
        "routes_sample_truncated": len(_as_list(api_routes.get("routes"))) > len(routes),
        "detection_notes": _as_list(
            api_routes.get("detection_notes")
        )[:MAX_DETECTION_NOTES_FOR_PROMPT],
    }


def _summarize_dependency_graph(dependency_graph: dict[str, Any]) -> dict[str, Any]:
    raw_nodes = _as_list(dependency_graph.get("nodes"))
    raw_edges = _as_list(dependency_graph.get("edges"))
    external_packages = dependency_graph.get("external_packages") or {}

    nodes = []
    for node in raw_nodes[:MAX_GRAPH_NODES_FOR_PROMPT]:
        if not isinstance(node, dict):
            continue
        nodes.append(
            {
                "id": node.get("id"),
                "label": node.get("label"),
                "package": node.get("package"),
                "imports_count": node.get("imports_count"),
                "imported_by_count": node.get("imported_by_count"),
                "external_imports": _as_list(node.get("external_imports"))[:12],
            }
        )

    edges = []
    for edge in raw_edges[:MAX_GRAPH_EDGES_FOR_PROMPT]:
        if not isinstance(edge, dict):
            continue
        edges.append(
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
            }
        )

    return {
        "nodes_in_graph": len(raw_nodes),
        "edges_in_graph": dependency_graph.get("total_internal_edges") or len(raw_edges),
        "files_analyzed": dependency_graph.get("files_analyzed"),
        "files_skipped": dependency_graph.get("files_skipped"),
        "parse_error_count": len(_as_list(dependency_graph.get("parse_errors"))),
        "external_packages": sorted(external_packages.keys())[:40]
        if isinstance(external_packages, dict)
        else [],
        "nodes_sample": nodes,
        "edges_sample": edges,
        "samples_truncated": len(raw_nodes) > len(nodes) or len(raw_edges) > len(edges),
        "detection_notes": _as_list(
            dependency_graph.get("detection_notes")
        )[:MAX_DETECTION_NOTES_FOR_PROMPT],
    }


def _summarize_architecture(architecture: dict[str, Any]) -> dict[str, Any]:
    scores = []
    for score in _as_list(architecture.get("all_scores"))[:8]:
        if not isinstance(score, dict):
            continue
        scores.append(
            {
                "pattern": score.get("pattern"),
                "score": score.get("score"),
            }
        )

    return {
        "primary_pattern": architecture.get("primary_pattern"),
        "description": architecture.get("description"),
        "confidence": architecture.get("confidence"),
        "evidence": _as_list(architecture.get("evidence"))[:12],
        "top_level_directories": _as_list(architecture.get("top_level_directories")),
        "ranked_scores": scores,
        "detection_notes": _as_list(
            architecture.get("detection_notes")
        )[:MAX_DETECTION_NOTES_FOR_PROMPT],
    }


def _parse_repo_summary(response_text: str) -> RepoSummary:
    cleaned = _strip_json_fence(response_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}") from exc

    try:
        return RepoSummary.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Gemini JSON did not match RepoSummary schema: {exc}") from exc


def _extract_response_text(response: Any) -> str:
    try:
        text = response.text
    except Exception:
        text = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                text = "".join(getattr(part, "text", "") for part in parts)

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Gemini returned an empty response.")
    return text.strip()


def _build_fallback_summary(parsed_data: dict[str, Any]) -> dict[str, Any]:
    manifest = _as_dict(parsed_data.get("file_manifest") or parsed_data.get("manifest"))
    tech_stack = _as_dict(parsed_data.get("tech_stack"))
    api_routes = _as_dict(parsed_data.get("api_routes") or parsed_data.get("routes"))
    dependency_graph = _as_dict(parsed_data.get("dependency_graph"))
    architecture = _as_dict(
        parsed_data.get("architecture_hints") or parsed_data.get("architecture")
    )

    repo_label = "/".join(
        part for part in [manifest.get("owner"), manifest.get("repo_name")] if part
    ) or "this repository"
    description = manifest.get("description")
    languages = _join_or_none(_as_list(tech_stack.get("languages")))
    frameworks = _join_or_none(_as_list(tech_stack.get("frameworks")))
    route_count = api_routes.get("total_routes") or len(_as_list(api_routes.get("routes")))
    pattern = architecture.get("primary_pattern") or "Unknown"
    confidence = architecture.get("confidence") or "low"
    total_files = manifest.get("total_files")

    purpose_sentences = []
    if description:
        purpose_sentences.append(f"{repo_label} appears to be {description}.")
    else:
        purpose_sentences.append(
            f"{repo_label} is a software repository analyzed from metadata."
        )
    if frameworks or languages:
        stack_sentence = (
            f"The deterministic parser identified {frameworks or 'no specific framework'}"
            f" on {languages or 'an unspecified language stack'}"
        )
        if total_files:
            stack_sentence += f" across {total_files} files"
        purpose_sentences.append(stack_sentence + ".")
    elif total_files:
        purpose_sentences.append(f"The manifest contains {total_files} files.")

    features = _fallback_features(tech_stack, api_routes, dependency_graph, architecture)

    return RepoSummary(
        project_purpose=" ".join(purpose_sentences[:3]),
        architecture_summary=(
            f"The parser detected '{pattern}' architecture with {confidence} confidence. "
            "This assessment is based on structured metadata such as file paths, "
            "detected frameworks, API route declarations, and dependency graph shape."
        ),
        key_features=features,
        strengths_and_weaknesses=(
            "Strengths: deterministic parser output is available for the stack, routes, "
            "dependency graph, and architectural hints. Weaknesses: the Gemini summary "
            "could not be generated, so this fallback avoids deeper product or design "
            "claims beyond the parsed metadata."
        ),
    ).model_dump(mode="json")


def _fallback_features(
    tech_stack: dict[str, Any],
    api_routes: dict[str, Any],
    dependency_graph: dict[str, Any],
    architecture: dict[str, Any],
) -> list[str]:
    features: list[str] = []
    frameworks = _join_or_none(_as_list(tech_stack.get("frameworks")))
    databases = _join_or_none(_as_list(tech_stack.get("databases")))
    infra = _join_or_none(_as_list(tech_stack.get("infra")))
    route_count = api_routes.get("total_routes") or len(_as_list(api_routes.get("routes")))
    graph_nodes = len(_as_list(dependency_graph.get("nodes")))
    pattern = architecture.get("primary_pattern")

    if route_count:
        features.append(f"API surface with {route_count} detected route(s)")
    if frameworks:
        features.append(f"Application framework stack using {frameworks}")
    if databases:
        features.append(f"Persistence layer signals for {databases}")
    if infra:
        features.append(f"Infrastructure and deployment hints including {infra}")
    if pattern and pattern != "Unknown":
        features.append(f"Repository structure consistent with {pattern}")
    if graph_nodes:
        features.append(f"Internal dependency graph with {graph_nodes} analyzed module(s)")

    defaults = [
        "Structured repository manifest suitable for UI exploration",
        "Deterministic tech stack detection from dependency and path metadata",
        "Architecture hints derived without sending raw source code to the LLM",
    ]
    for item in defaults:
        if len(features) >= 3:
            break
        features.append(item)

    return features[:5]


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    value = _jsonable(value)
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    value = _jsonable(value)
    return value if isinstance(value, list) else []


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    return match.group(1).strip() if match else stripped


def _has_real_api_key(value: str | None) -> bool:
    if not value:
        return False
    stripped = value.strip()
    placeholders = {
        "your_gemini_api_key_here",
        "your_google_api_key_here",
        "AIzaSy_your_key_here",
    }
    return stripped not in placeholders and not stripped.lower().startswith("your_")


def _prefix(value: Any, length: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:length]


def _join_or_none(values: list[Any]) -> str | None:
    cleaned = [str(value) for value in values if value]
    return ", ".join(cleaned) if cleaned else None
