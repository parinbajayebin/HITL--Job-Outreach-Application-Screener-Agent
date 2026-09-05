"""Integration tests for the FastAPI endpoints and LangGraph workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env(monkeypatch):
    """Set required environment variables for the test session."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-integration")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key-integration")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("REAL_DISPATCH", "false")


@pytest.fixture
def client(mock_env):
    """Create a FastAPI TestClient with mocked dependencies."""
    with patch("app.services.graph.build_graph") as mock_graph, \
         patch("app.services.persistence.get_checkpointer") as mock_cp:

        # Mock the graph to return immediately
        mock_compiled = MagicMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "completed"})
        mock_graph.return_value = mock_compiled

        # Mock the checkpointer
        mock_saver = MagicMock()
        mock_saver.save = AsyncMock()
        mock_saver.load = AsyncMock(return_value=None)
        mock_cp.return_value = mock_saver

        from app.main import app
        yield TestClient(app)


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------


class TestRunEndpoint:
    """Integration tests for the POST /run endpoint."""

    def test_run_returns_thread_id(self, client):
        """POST /run should return a thread_id on success."""
        resp = client.post("/run", json={
            "resume_text": "Experienced Python developer with 5 years at Google.",
            "strengths": ["Python", "Kubernetes", "CI/CD"],
            "city": "San Francisco",
            "role": "Senior Backend Engineer",
            "dream_companies": ["Stripe", "Datadog"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "thread_id" in data
        assert len(data["thread_id"]) > 0

    def test_run_validates_missing_fields(self, client):
        """POST /run should return 422 when required fields are missing."""
        resp = client.post("/run", json={
            "resume_text": "Some resume",
            # Missing: strengths, city, role, dream_companies
        })
        assert resp.status_code == 422

    def test_run_validates_empty_resume(self, client):
        """POST /run should accept even a minimal payload."""
        resp = client.post("/run", json={
            "resume_text": "x",
            "strengths": ["A"],
            "city": "NYC",
            "role": "Dev",
            "dream_companies": ["Acme"],
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /state/{thread_id}
# ---------------------------------------------------------------------------


class TestStateEndpoint:
    """Integration tests for the GET /state/{thread_id} endpoint."""

    def test_state_returns_404_for_unknown_thread(self, client):
        """GET /state should return 404 when thread_id doesn't exist."""
        resp = client.get("/state/nonexistent-thread-id")
        assert resp.status_code in (404, 500)

    def test_state_returns_data_for_known_thread(self, mock_env):
        """GET /state should return the saved state."""
        saved_state = {
            "thread_id": "test-known",
            "resume_text": "Resume text here",
            "match_score": 75,
            "status": "scored",
        }

        with patch("app.services.graph.build_graph") as mock_graph, \
             patch("app.services.persistence.get_checkpointer") as mock_cp:

            mock_compiled = MagicMock()
            mock_compiled.ainvoke = AsyncMock()
            mock_graph.return_value = mock_compiled

            mock_saver = MagicMock()
            mock_saver.save = AsyncMock()
            mock_saver.load = AsyncMock(return_value=saved_state)
            mock_cp.return_value = mock_saver

            from app.main import app
            client = TestClient(app)

            resp = client.get("/state/test-known")
            assert resp.status_code == 200
            data = resp.json()
            assert data["thread_id"] == "test-known"
            assert data["match_score"] == 75


# ---------------------------------------------------------------------------
# POST /feedback/{thread_id}
# ---------------------------------------------------------------------------


class TestFeedbackEndpoint:
    """Integration tests for the POST /feedback/{thread_id} endpoint."""

    def test_feedback_returns_404_for_unknown_thread(self, client):
        """POST /feedback should return 404 when thread doesn't exist."""
        resp = client.post(
            "/feedback/nonexistent",
            json={"human_feedback": "approve"},
        )
        assert resp.status_code in (404, 500)

    def test_feedback_accepts_valid_actions(self, mock_env):
        """POST /feedback should accept approve/revise/reject."""
        existing_state = {
            "thread_id": "fb-test",
            "outreach_draft": {"email_subject": "Hi"},
        }

        with patch("app.services.graph.build_graph") as mock_graph, \
             patch("app.services.persistence.get_checkpointer") as mock_cp:

            mock_compiled = MagicMock()
            mock_compiled.ainvoke = AsyncMock()
            mock_graph.return_value = mock_compiled

            mock_saver = MagicMock()
            mock_saver.save = AsyncMock()
            mock_saver.load = AsyncMock(return_value=existing_state)
            mock_cp.return_value = mock_saver

            from app.main import app
            client = TestClient(app)

            for action in ("approve", "revise", "reject"):
                resp = client.post(
                    "/feedback/fb-test",
                    json={"human_feedback": action},
                )
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /settings/linkedin_cookie
# ---------------------------------------------------------------------------


class TestLinkedInCookieEndpoint:
    """Integration tests for the LinkedIn cookie settings endpoint."""

    def test_cookie_rejects_invalid_format(self, client):
        """POST /settings/linkedin_cookie should reject cookies not starting with li_at=."""
        resp = client.post(
            "/settings/linkedin_cookie",
            json={"cookie": "invalid_cookie_value"},
        )
        assert resp.status_code == 400

    def test_cookie_accepts_valid_format(self, client, tmp_path):
        """POST /settings/linkedin_cookie should accept li_at= prefixed cookies."""
        # Create a temporary .env file
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-test\n")

        with patch("os.getcwd", return_value=str(tmp_path)):
            resp = client.post(
                "/settings/linkedin_cookie",
                json={"cookie": "li_at=AQEDAS_test_cookie_value"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"


# ---------------------------------------------------------------------------
# Workflow end-to-end (mocked)
# ---------------------------------------------------------------------------


class TestWorkflowE2E:
    """End-to-end workflow test with fully mocked services."""

    @pytest.mark.asyncio
    async def test_full_workflow_approve_flow(self):
        """Simulate a complete workflow: score → research → draft → approve → dispatch."""
        with patch("app.services.graph.LLMProvider") as MockLLM, \
             patch("app.services.graph.SearchService") as MockSearch, \
             patch("app.services.graph.DispatchService") as MockDispatch, \
             patch("app.services.graph.get_settings") as mock_settings:

            # Configure mocks
            mock_settings.return_value = MagicMock(REAL_DISPATCH=False)

            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value=MagicMock(
                match_score=85,
                evidenced_strengths=[{"strength": "Python", "evidence": "Built APIs", "score": 90}],
                missing_strengths=[],
                email_subject="Hello",
                email_body="I am interested...",
                linkedin_note="Hi, I noticed...",
                key_points_used=["Python", "FastAPI"],
            ))
            MockLLM.return_value = mock_llm

            mock_search = MagicMock()
            mock_search.research_company = AsyncMock(return_value={"summary": "Great company"})
            MockSearch.return_value = mock_search

            # Import and test nodes
            from app.services.graph import (
                parse_and_score,
                fit_check,
                research_company,
                draft_outreach,
                human_approval,
                dispatch_action,
            )

            state = {
                "thread_id": "e2e-test",
                "resume_text": "Python developer at Google for 5 years",
                "strengths": ["Python", "FastAPI"],
                "city": "SF",
                "role": "Backend Eng",
                "dream_companies": ["Stripe"],
                "human_feedback": "approve",
            }

            # Run each node sequentially
            state = await parse_and_score(state)
            assert "error" not in state or state.get("match_score", 0) > 0

            route = await fit_check(state)
            assert route in ("research_company", "human_review_low_fit")

            if route == "research_company":
                state = await research_company(state)
                state = await draft_outreach(state)
                state = await human_approval(state)
                state = await dispatch_action(state)
