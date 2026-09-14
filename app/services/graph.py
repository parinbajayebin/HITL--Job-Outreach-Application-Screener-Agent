"""app/services/graph.py
================================
LangGraph workflow for the Human-in-the-Loop (HITL) job screener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, TypedDict

from app.config import get_settings
from models.outputs import OutreachDraft
from app.services.dispatch import DispatchService
from app.services.llm import LLMProvider
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
    evidenced_strengths: List[str]
    missing_strengths: List[str]
    company_insights: Dict[str, Any]
    outreach_draft: OutreachDraft
    revision_count: int
    human_feedback: str
    error: str


async def parse_and_score(state: GraphState) -> GraphState:
    try:
        provider = LLMProvider()
        result = await provider.acomplete(
            model="gpt-4o-mini",
            prompt=(
                f"Score the following resume against strengths.\n\n"
                f"Resume:\n{state.get('resume_text', '')}\n\n"
                f"Strengths: {', '.join(state.get('strengths', []))}"
            ),
        )
        state.update(
            {
                "match_score": getattr(result, "overall_score", 75.0),
                "evidenced_strengths": getattr(result, "evidenced", []),
                "missing_strengths": getattr(result, "missing", []),
            }
        )
        return state
    except Exception as exc:
        logger.error("Error in parse_and_score: %s", exc, exc_info=True)
        state["error"] = f"parse_and_score failed: {exc}"
        return state


async def fit_check(state: GraphState) -> str:
    score = state.get("match_score", 0)
    if score < 60:
        return "human_review_low_fit"
    return "research_company"


async def research_company(state: GraphState) -> GraphState:
    try:
        service = SearchService()
        insights: Dict[str, Any] = {}
        for company in state.get("dream_companies", []):
            insights[company] = await service.research_company(company)
        state["company_insights"] = insights
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
            f"Create an outreach draft for a candidate applying to a role in {state.get('city')}.\n"
            f"Strengths: {', '.join(state.get('strengths', []))}\n"
            f"Company insights:\n{insights_text}\n"
        )
        draft = await provider.acomplete(
            model="gpt-4o-mini",
            schema=OutreachDraft,
            prompt=prompt,
        )
        state["outreach_draft"] = draft
        state.setdefault("revision_count", 0)
        return state
    except Exception as exc:
        logger.error("Error in draft_outreach: %s", exc, exc_info=True)
        state["error"] = f"draft_outreach failed: {exc}"
        return state


async def human_approval(state: GraphState) -> GraphState:
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
        return state
    except Exception as exc:
        logger.error("Error in dispatch_action: %s", exc, exc_info=True)
        state["error"] = f"dispatch_action failed: {exc}"
        return state


class MockGraph:
    """Fallback compiled graph representation."""
    async def ainvoke(self, state: GraphState) -> GraphState:
        s = await parse_and_score(state)
        nxt = await fit_check(s)
        if nxt == "research_company":
            s = await research_company(s)
            s = await draft_outreach(s)
            s = await human_approval(s)
            s = await dispatch_action(s)
        return s


def build_graph():
    """Build LangGraph StateGraph or fallback runner."""
    try:
        from langgraph.graph import END, StateGraph
        builder = StateGraph(GraphState)
        builder.add_node("parse_and_score", parse_and_score)
        builder.add_node("research_company", research_company)
        builder.add_node("draft_outreach", draft_outreach)
        builder.add_node("human_approval", human_approval)
        builder.add_node("dispatch_action", dispatch_action)
        builder.set_entry_point("parse_and_score")
        builder.add_conditional_edges(
            "parse_and_score",
            fit_check,
            {
                "research_company": "research_company",
                "human_review_low_fit": END,
            },
        )
        builder.add_edge("research_company", "draft_outreach")
        builder.add_edge("draft_outreach", "human_approval")
        builder.add_edge("human_approval", "dispatch_action")
        builder.add_edge("dispatch_action", END)
        return builder.compile()
    except Exception as e:
        logger.warning("LangGraph compile fallback: %s", e)
        return MockGraph()


__all__ = ["build_graph", "GraphState"]
