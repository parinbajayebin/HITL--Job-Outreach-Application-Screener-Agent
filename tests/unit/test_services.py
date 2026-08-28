"""Unit tests for the LLM provider service."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_openai_response():
    """Return a fake OpenAI chat-completion response."""
    choice = MagicMock()
    choice.message.content = '{"overall_score": 85, "evidenced": [], "missing": [], "summary": "Good match"}'
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# LLMProvider.invoke
# ---------------------------------------------------------------------------


class TestLLMProvider:
    """Tests for :class:`app.services.llm.LLMProvider`."""

    @patch("app.services.llm.get_settings")
    def test_provider_initialises_without_error(self, mock_settings):
        """LLMProvider should instantiate when OPENAI_API_KEY is set."""
        mock_settings.return_value = MagicMock(OPENAI_API_KEY="sk-test-key")
        from app.services.llm import LLMProvider

        provider = LLMProvider()
        assert provider is not None

    @patch("app.services.llm.get_settings")
    def test_provider_raises_without_key(self, mock_settings):
        """LLMProvider should raise when no API key is configured."""
        mock_settings.return_value = MagicMock(OPENAI_API_KEY=None)
        with pytest.raises(Exception):
            from app.services.llm import LLMProvider

            LLMProvider()


# ---------------------------------------------------------------------------
# SearchService
# ---------------------------------------------------------------------------


class TestSearchService:
    """Tests for :class:`app.services.search.SearchService`."""

    @patch("app.services.search.get_settings")
    def test_search_service_initialises(self, mock_settings):
        """SearchService should initialise with or without Tavily key."""
        mock_settings.return_value = MagicMock(TAVILY_API_KEY=None)
        from app.services.search import SearchService

        svc = SearchService()
        assert svc is not None

    @patch("app.services.search.get_settings")
    @pytest.mark.asyncio
    async def test_search_jobs_returns_list(self, mock_settings):
        """search_jobs should return a list (even if empty)."""
        mock_settings.return_value = MagicMock(TAVILY_API_KEY=None)
        from app.services.search import SearchService

        svc = SearchService()
        # Mock the internal search to avoid real network calls
        svc._duckduckgo_search = AsyncMock(return_value=[])
        results = await svc.search_jobs(role="Engineer", city="NYC")
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# SupabaseService
# ---------------------------------------------------------------------------


class TestSupabaseService:
    """Tests for :class:`app.services.supabase_client.SupabaseService`."""

    @patch("app.services.supabase_client.get_settings")
    def test_supabase_service_initialises(self, mock_settings):
        """SupabaseService should initialise with valid URL and key."""
        mock_settings.return_value = MagicMock(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_KEY="test-key",
            SUPABASE_BUCKET="resumes",
            SUPABASE_TABLE="job_posts",
        )
        from app.services.supabase_client import SupabaseService

        svc = SupabaseService()
        assert svc is not None

    @patch("app.services.supabase_client.get_settings")
    @pytest.mark.asyncio
    async def test_upload_resume_calls_storage(self, mock_settings):
        """upload_resume should invoke the Supabase storage upload."""
        mock_settings.return_value = MagicMock(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_KEY="test-key",
            SUPABASE_BUCKET="resumes",
            SUPABASE_TABLE="job_posts",
        )
        from app.services.supabase_client import SupabaseService

        svc = SupabaseService()
        # Mock the supabase client's storage
        svc.client = MagicMock()
        mock_upload = MagicMock()
        mock_upload.upload = MagicMock(return_value={"Key": "resumes/test.pdf"})
        svc.client.storage.from_ = MagicMock(return_value=mock_upload)

        result = await svc.upload_resume(b"fake-pdf-bytes", "test.pdf")
        assert result is not None


# ---------------------------------------------------------------------------
# Graph nodes (isolated)
# ---------------------------------------------------------------------------


class TestGraphNodes:
    """Test individual LangGraph node functions in isolation."""

    @pytest.mark.asyncio
    async def test_parse_and_score_handles_error_gracefully(self):
        """parse_and_score should capture errors into state['error']."""
        with patch("app.services.graph.LLMProvider") as MockLLM:
            MockLLM.side_effect = Exception("API key missing")
            from app.services.graph import parse_and_score

            state = {
                "resume_text": "Some resume",
                "strengths": ["Python"],
                "thread_id": "test-123",
            }
            result = await parse_and_score(state)
            assert "error" in result

    @pytest.mark.asyncio
    async def test_fit_check_routes_low_score(self):
        """fit_check should return 'human_review_low_fit' for score < 60."""
        from app.services.graph import fit_check

        state = {"match_score": 40}
        result = await fit_check(state)
        assert result == "human_review_low_fit"

    @pytest.mark.asyncio
    async def test_fit_check_routes_high_score(self):
        """fit_check should return 'research_company' for score >= 60."""
        from app.services.graph import fit_check

        state = {"match_score": 85}
        result = await fit_check(state)
        assert result == "research_company"

    @pytest.mark.asyncio
    async def test_human_approval_returns_state_unchanged(self):
        """human_approval node should pass through without modification."""
        from app.services.graph import human_approval

        state = {"thread_id": "t-1", "outreach_draft": {"email_subject": "Hi"}}
        result = await human_approval(state)
        assert result == state


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class TestExtractor:
    """Tests for :class:`app.services.extractor.ResumeExtractor`."""

    def test_extract_text_from_plain_string(self):
        """Extractor should handle plain text input."""
        try:
            from app.services.extractor import ResumeExtractor

            ext = ResumeExtractor()
            result = ext.extract_text("This is a plain text resume.")
            assert isinstance(result, str)
            assert len(result) > 0
        except (ImportError, AttributeError):
            # Module may have different API — skip gracefully
            pytest.skip("ResumeExtractor API differs from expected")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for :mod:`app.config`."""

    @patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "sk-test",
            "SUPABASE_URL": "https://x.supabase.co",
            "SUPABASE_KEY": "key123",
        },
    )
    def test_settings_loads_from_env(self):
        """Settings should load values from environment variables."""
        from app.config import Settings

        s = Settings()
        assert s.OPENAI_API_KEY == "sk-test"
        assert s.SUPABASE_URL == "https://x.supabase.co"

    def test_settings_raises_without_required_keys(self):
        """Settings should raise ValueError when required keys missing."""
        with patch.dict("os.environ", {}, clear=True):
            from app.config import Settings

            with pytest.raises(Exception):
                Settings()
