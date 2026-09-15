"""app/services/graph.py
================================
LangGraph workflow for the Human-in-the-Loop (HITL) job screener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, TypedDict

from app.config import get_settings
from models.outputs import OutreachDraft, StrengthScoreOutput
from app.services.dispatch import DispatchService
from app.services.llm import LLMProvider
from app.services.persistence import get_checkpointer
from app.services.search import SearchService

logger = logging.getLogger(__name__)


class GraphState(TypedDict, total=False):
    """Alias for the workflow state."""

    thread_id: str
    resume_text: str
    strengths: List[str]
    city: str
    role: str
    dream_companies: List[str]

    match_score: float
    evidenced_strengths: List[Any]
    missing_strengths: List[str]
    company_insights: Dict[str, Any]
    outreach_draft: OutreachDraft
    revision_count: int
    human_feedback: str
    status: str
    error: str


async def parse_and_score(state: GraphState) -> GraphState:
    try:
        provider = LLMProvider()
        prompt = (
            f"Score the following resume against the user's declared strengths.\n\n"
            f"Candidate Target Role: {state.get('role', '')}\n"
            f"Target City: {state.get('city', '')}\n\n"
            f"Declared Strengths:\n{', '.join(state.get('strengths', []))}\n\n"
            f"Resume Text:\n{state.get('resume_text', '')[:4000]}"
        )
        result = await provider.invoke(
            prompt=prompt,
            system_prompt="You are an expert technical recruiter assessing candidate resumes against stated strengths.",
            output_schema=StrengthScoreOutput,
        )
        if isinstance(result, StrengthScoreOutput):
            state["match_score"] = result.overall_score
            state["evidenced_strengths"] = [e.model_dump() for e in result.evidenced]
            state["missing_strengths"] = result.missing
        else:
            state["match_score"] = 75.0
            state["evidenced_strengths"] = state.get("strengths", [])
            state["missing_strengths"] = []

        state["status"] = "scored"
        return state
    except Exception as exc:
        logger.error("Error in parse_and_score: %s", exc, exc_info=True)
        state["error"] = f"parse_and_score failed: {exc}"
        state["status"] = "failed"
        return state


async def fit_check(state: GraphState) -> str:
    score = state.get("match_score", 0)
    if score < 60:
        state["status"] = "awaiting_human"
        return "human_review_low_fit"
    return "research_company"


async def research_company(state: GraphState) -> GraphState:
    try:
        service = SearchService()
        insights: Dict[str, Any] = {}
        for company in state.get("dream_companies", []):
            try:
                insights[company] = await service.research_company(company)
            except Exception as e:
                insights[company] = {"summary": f"Could not fetch research: {e}"}
        state["company_insights"] = insights
        state["status"] = "research_done"
        return state
    except Exception as exc:
        logger.error("Error in research_company: %s", exc, exc_info=True)
        state["error"] = f"research_company failed: {exc}"
        return state


async def draft_outreach(state: GraphState) -> GraphState:
    try:
        provider = LLMProvider()
        insights = state.get("company_insights", {})
        insights_text = "\n".join(f"{k}: {v}" for k, v in insights.items())
        prompt = (
            f"Create personalized cold outreach for a candidate applying to {state.get('role')} in {state.get('city')}.\n"
            f"Candidate Strengths: {', '.join(state.get('strengths', []))}\n"
            f"Target Companies Research:\n{insights_text}\n"
            "Draft a compelling, professional email and a short LinkedIn connection request note."
        )
        draft = await provider.invoke(
            prompt=prompt,
            system_prompt="You are an executive talent agent writing personalized cold outreach.",
            output_schema=OutreachDraft,
        )
        state["outreach_draft"] = draft.model_dump() if hasattr(draft, "model_dump") else draft
        state.setdefault("revision_count", 0)
        state["status"] = "drafted"
        return state
    except Exception as exc:
        logger.error("Error in draft_outreach: %s", exc, exc_info=True)
        state["error"] = f"draft_outreach failed: {exc}"
        return state


async def human_approval(state: GraphState) -> GraphState:
    state["status"] = "awaiting_human"
    return state


async def dispatch_action(state: GraphState) -> GraphState:
    try:
        settings = get_settings()
        service = DispatchService()
        draft: Any = state.get("outreach_draft", {})
        payload = {
            "subject": getattr(draft, "email_subject", draft.get("email_subject", "") if isinstance(draft, dict) else ""),
            "body": getattr(draft, "email_body", draft.get("email_body", "") if isinstance(draft, dict) else ""),
            "linkedin_note": getattr(draft, "linkedin_note", draft.get("linkedin_note", "") if isinstance(draft, dict) else ""),
            "to": state.get("dream_companies"),
        }
        if getattr(settings, "REAL_DISPATCH", False):
            await service.dispatch(payload)
            logger.info("Outreach dispatched for thread %s", state.get("thread_id"))
        else:
            logger.info("REAL_DISPATCH disabled - outreach payload: %s", payload)
        state["status"] = "approved"
        return state
    except Exception as exc:
        logger.error("Error in dispatch_action: %s", exc, exc_info=True)
        state["error"] = f"dispatch_action failed: {exc}"
        return state


class MockGraph:
    """Sequential runner that persists checkpoint state between nodes."""
    async def ainvoke(self, state: GraphState) -> GraphState:
        cp = get_checkpointer()
        tid = state.get("thread_id", "")
        
        # 1. Parse & Score
        state = await parse_and_score(state)
        if tid:
            await cp.save(tid, state)
        if state.get("error"):
            return state

        # 2. Fit Check
        nxt = await fit_check(state)
        if nxt == "human_review_low_fit":
            state["status"] = "awaiting_human"
            if tid:
                await cp.save(tid, state)
            return state

        # 3. Company Research
        state = await research_company(state)
        if tid:
            await cp.save(tid, state)

        # 4. Draft Outreach
        state = await draft_outreach(state)
        if tid:
            await cp.save(tid, state)

        # 5. Wait for Human Feedback
        state["status"] = "awaiting_human"
        if tid:
            await cp.save(tid, state)
            
        return state


def build_graph():
    return MockGraph()


__all__ = ["build_graph", "GraphState"]
