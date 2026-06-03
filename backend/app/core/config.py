"""
app/core/config.py

Application settings loaded from environment variables / .env file.
Uses pydantic-settings so every setting is typed and validated at startup.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration object for DevTwin backend.

    All values are read from environment variables or the .env file at startup.
    If a required variable is missing, the app will refuse to start with a
    clear validation error — no silent misconfiguration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore unknown env vars (e.g. system variables)
    )

    # ─── Database ────────────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description=(
            "Async PostgreSQL connection string. "
            "Must use the psycopg v3 driver: postgresql+psycopg://..."
        ),
    )

    # ─── GitHub ──────────────────────────────────────────────────────────────
    github_token: str | None = Field(
        default=None,
        description=(
            "GitHub Personal Access Token. "
            "Optional but strongly recommended — raises rate limit from 60 to 5,000 req/hr."
        ),
    )
    github_api_base: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub REST API. Override for GitHub Enterprise.",
    )

    # ─── Gemini ──────────────────────────────────────────────────────────────
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        description=(
            "API key for the Gemini summarizer. "
            "Accepts either GEMINI_API_KEY or GOOGLE_API_KEY."
        ),
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Gemini model used by the Step 3 summarizer. "
            "Use gemini-2.5-flash for speed/cost or gemini-2.5-pro for deeper summaries."
        ),
    )

    # ─── Neo4j Knowledge Graph ───────────────────────────────────────────────
    neo4j_uri: str | None = Field(
        default=None,
        description=(
            "Neo4j connection URI for Phase 3 Knowledge Graph. "
            "AuraDB usually uses neo4j+s://<instance>.databases.neo4j.io."
        ),
    )
    neo4j_user: str | None = Field(
        default="neo4j",
        description="Neo4j database username.",
    )
    neo4j_password: str | None = Field(
        default=None,
        description="Neo4j database password.",
    )

    # ─── Clone settings ───────────────────────────────────────────────────────
    clone_base_dir: str = Field(
        default="/tmp/devtwin_repos",
        description=(
            "Absolute path to the directory where repositories are shallow-cloned. "
            "The directory is created automatically if it does not exist."
        ),
    )
    enable_clone: bool = Field(
        default=True,
        description=(
            "When True, performs a local git clone --depth=1 after fetching the manifest. "
            "Set to False to operate purely via the GitHub API (no local disk usage)."
        ),
    )

    # ─── App settings ─────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Runtime environment. Controls debug output and error detail.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Python logging level.",
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure the URL uses the psycopg v3 async driver."""
        if not v.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DATABASE_URL must use the psycopg v3 driver. "
                "Example: postgresql+psycopg://user:pass@localhost:5432/dbname"
            )
        return v

    @field_validator("neo4j_uri")
    @classmethod
    def validate_neo4j_uri(cls, v: str | None) -> str | None:
        """Ensure configured Neo4j URIs use an official driver scheme."""
        if v is None or not v.strip():
            return None

        normalized = v.strip()
        allowed_schemes = (
            "neo4j://",
            "neo4j+s://",
            "neo4j+ssc://",
            "bolt://",
            "bolt+s://",
            "bolt+ssc://",
        )
        if not normalized.startswith(allowed_schemes):
            raise ValueError(
                "NEO4J_URI must start with neo4j://, neo4j+s://, neo4j+ssc://, "
                "bolt://, bolt+s://, or bolt+ssc://"
            )
        return normalized


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached Settings singleton.

    Using lru_cache means .env is only parsed once per process lifetime,
    not on every request. Call `get_settings.cache_clear()` in tests to
    reload settings with different env vars.
    """
    return Settings()
