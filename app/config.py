"""Application configuration loaded from environment / .env file.

Uses *pydantic-settings* ``BaseSettings`` so every field can be
overridden via an environment variable of the same name.
A cached ``get_settings()`` helper guarantees a singleton instance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the HITL Job Outreach Agent.

    All values are read from the process environment (or a ``.env``
    file in the project root).  Sensitive keys are typed as
    ``Optional[str]`` so the app can still start in limited-
    functionality mode when they are absent.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── LLM ─────────────────────────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = None

    # ── SMTP (email dispatch) ───────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASS: Optional[str] = None
    REAL_DISPATCH: bool = False  # flip to True to actually send mail

    # ── LinkedIn ────────────────────────────────────────────────────
    LINKEDIN_API_TOKEN: Optional[str] = None
    LINKEDIN_SELENIUM_COOKIE: Optional[str] = None  # li_at cookie value

    # ── Web Search ──────────────────────────────────────────────────
    TAVILY_API_KEY: Optional[str] = None

    # ── Supabase ────────────────────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_BUCKET: str = "resumes"
    SUPABASE_TABLE: str = "job_posts"

    # ── Observability ───────────────────────────────────────────────
    LANGSMITH_API_KEY: Optional[str] = None

    # ── Security ────────────────────────────────────────────────────
    ENABLE_ENCRYPTION: bool = False

    # ── Networking ──────────────────────────────────────────────────
    APP_PORT: int = 8000
    UI_PORT: int = 8501

    # ── Startup validators ──────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_required_keys(self) -> "Settings":
        """Ensure at least one LLM key and the Supabase credentials are set."""
        # At least one LLM provider must be configured
        if not self.OPENAI_API_KEY:
            raise ValueError(
                "At least one LLM API key must be provided. "
                "Set OPENAI_API_KEY in your .env file."
            )

        # Supabase is mandatory for resume / job-post storage
        if not self.SUPABASE_URL or not self.SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY are required. "
                "Set them in your .env file."
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton ``Settings`` instance.

    The first call reads environment / ``.env``; subsequent calls
    return the same object without re-parsing.
    """
    return Settings()  # type: ignore[call-arg]
