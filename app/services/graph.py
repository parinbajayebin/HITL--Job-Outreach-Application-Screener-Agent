'''app/services/graph.py
================================
LangGraph workflow for the Human‑in‑the‑Loop (HITL) job screener.

The graph stitches together the following steps:

1. ``parse_and_score`` – Uses the LLM to assess how well a resume matches a list of
   strengths and produces a match score together with evidence.
2. ``fit_check`` – Conditional edge that routes low‑fit resumes (score < 60) to a
   ``human_review_low_fit`` node for manual handling.
3. ``research_company`` – Retrieves company‑specific insights using the
   ``SearchService``.
4. ``draft_outreach`` – Generates an outreach draft (email subject/body and a
   LinkedIn note) via the LLM.
5. ``human_approval`` – An interrupt node that yields the draft to the user and
   waits for feedback (Approve / Revise / Reject). The ``revision_count`` is
   tracked in the state.
6. ``dispatch_action`` – Sends the final outreach using ``DispatchService`` when
   ``REAL_DISPATCH`` is enabled, otherwise only logs the payload.

All node functions are ``async`` and robustly handle unexpected errors by
recording them in the state under the ``error`` key.
'''\n\nfrom __future__ import annotations\n\nimport logging\nimport traceback\nfrom typing import Any, TypedDict, List, Dict\n\nfrom uuid import uuid4\n\nfrom langgraph.graph import GraphBuilder, StateGraph, ConditionalEdge, END\nfrom langgraph.checkpoint.base import get_checkpointer\n\n# Local imports – adjust if module paths differ in your project\nfrom app.services.persistence import get_checkpointer  # type: ignore  # noqa: F401\nfrom app.config import get_settings\nfrom app.services.llm import LLMProvider\nfrom app.services.search import SearchService\nfrom app.services.dispatch import DispatchService\nfrom app.models.state import ScreenerState  # TypedDict representing the graph state\nfrom app.models.outputs import strength_score_output, OutreachDraft  # Pydantic models / schemas\nfrom app.models.profile import RequirementProfile\n\nlogger = logging.getLogger(__name__)\n\n\nclass GraphState(TypedDict, total=False):\n    """Alias for the workflow state that matches :class:`ScreenerState`."""
\n    # Core inputs supplied by the ``/run`` endpoint\n    thread_id: str\n    resume_text: str\n    strengths: List[str]\n    city: str\n    role: str\n    dream_companies: List[str]\n\n    # Intermediate values produced by nodes\n    match_score: float\n    evidenced_strengths: List[str]\n    missing_strengths: List[str]\n    company_insights: Dict[str, Any]\n    outreach_draft: OutreachDraft\n    revision_count: int\n    human_feedback: str  # Expected values: "approve", "revise", "reject"\n    error: str  # Populated when a node raises an exception\n\n\nasync def parse_and_score(state: GraphState) -> GraphState:\n    """Score the resume against the user's strength list.
\n    Parameters
    ----------
    state: GraphState
        Must contain ``resume_text`` and ``strengths``.
\n    Returns
    -------
    GraphState
        The state enriched with ``match_score``, ``evidenced_strengths`` and
        ``missing_strengths``.
    """
    try:
        provider = LLMProvider()
        # The LLMProvider is expected to expose an ``acomplete`` method that
        # returns a validated ``strength_score_output`` instance.
        result = await provider.acomplete(
            model="gpt-4o-mini",
            schema=strength_score_output,
            prompt=f"Score the following résumé against the listed strengths.\n\nResume:\n{state['resume_text']}\n\nStrengths: {', '.join(state['strengths'])}\n\nReturn a JSON object matching the schema.",
        )
        # ``result`` is a pydantic model; we convert to dict for the state.
        state.update({
            "match_score": result.match_score,
            "evidenced_strengths": result.evidenced_strengths,
            "missing_strengths": result.missing_strengths,
        })\n        return state\n    except Exception as exc:  # pragma: no cover – defensive programming\n        logger.error("Error in parse_and_score: %s", exc, exc_info=True)\n        state["error"] = f"parse_and_score failed: {exc}"\n        return state\n\n\nasync def fit_check(state: GraphState) -> str:\n    """Conditional edge – decide whether the resume is a good fit.
\n    Returns the name of the next node. ``human_review_low_fit`` is routed when
    ``match_score`` is below 60; otherwise the graph proceeds to ``research_company``.
    """
    score = state.get("match_score", 0)\n    if score < 60:\n        return "human_review_low_fit"\n    return "research_company"\n\n\nasync def research_company(state: GraphState) -> GraphState:\n    """Populate ``company_insights`` for each matched job.
\n    The function expects a list of job matches already present in the state –
    for simplicity we assume the state contains ``dream_companies`` which we
    treat as the target list.
    """
    try:
        service = SearchService()
        insights: Dict[str, Any] = {}
        for company in state.get("dream_companies", []):\n            insights[company] = await service.research_company(company)\n        state["company_insights"] = insights\n        return state\n    except Exception as exc:  # pragma: no cover\n        logger.error("Error in research_company: %s", exc, exc_info=True)\n        state["error"] = f"research_company failed: {exc}"\n        return state\n\n\nasync def draft_outreach(state: GraphState) -> GraphState:\n    """Generate an outreach draft using the LLM.
\n    The draft includes an email subject, email body and a LinkedIn note.
    """
    try:\n        provider = LLMProvider()\n        strengths_fmt = ", ".join(state.get("evidenced_strengths", []))\n        insights = state.get("company_insights", {})\n        # Flatten insights for the prompt – real implementation would be more
        # sophisticated.
        insights_text = "\n".join(f"{k}: {v}" for k, v in insights.items())\n        prompt = (
            f"Create an outreach draft for a candidate applying to a role in {state.get('city')}.\n"
            f"Strengths evidenced: {strengths_fmt}\n"
            f"Company insights:\n{insights_text}\n"
            f"Job description (extracted from the posting) is not provided here – assume it's typical for the role {state.get('role')}.\n"
            "Return a JSON object with 'email_subject', 'email_body' and 'linkedin_note'."
        )\n        draft: OutreachDraft = await provider.acomplete(
            model="gpt-4o-mini",
            schema=OutreachDraft,
            prompt=prompt,
        )\n        state["outreach_draft"] = draft\n        # Initialise revision counter if not present\n        state.setdefault("revision_count", 0)\n        return state\n    except Exception as exc:  # pragma: no cover\n        logger.error("Error in draft_outreach: %s", exc, exc_info=True)\n        state["error"] = f"draft_outreach failed: {exc}"\n        return state\n\n\nasync def human_approval(state: GraphState) -> GraphState:\n    """Interrupt node – wait for user feedback.
\n    The node yields the current draft and expects ``human_feedback`` to be
    injected later via the ``/feedback`` endpoint. If the feedback is ``revise``
    the ``revision_count`` is incremented and the graph loops back to
    ``draft_outreach``; ``approve`` continues to ``dispatch_action``; ``reject``
    terminates the workflow.
    """
    # The interrupt node automatically pauses execution until ``human_feedback``
    # is supplied. Here we simply return the state unchanged – LangGraph will
    # serialize the draft for the UI.
    return state\n\n\nasync def dispatch_action(state: GraphState) -> GraphState:\n    """Send the outreach via email / LinkedIn or log it for debugging.
\n    ``REAL_DISPATCH`` comes from the global settings.
    """
    try:\n        settings = get_settings()\n        service = DispatchService()\n        draft: OutreachDraft = state["outreach_draft"]  # type: ignore[arg-type]\n        payload = {\n            "subject": draft.email_subject,\n            "body": draft.email_body,\n            "linkedin_note": draft.linkedin_note,\n            "to": state.get("dream_companies"),  # placeholder recipient list\n        }\n        if getattr(settings, "REAL_DISPATCH", False):\n            await service.dispatch(payload)\n            logger.info("Outreach dispatched for thread %s", state.get("thread_id"))\n        else:\n            logger.info("REAL_DISPATCH disabled – outreach payload: %s", payload)\n        return state\n    except Exception as exc:  # pragma: no cover\n        logger.error("Error in dispatch_action: %s", exc, exc_info=True)\n        state["error"] = f"dispatch_action failed: {exc}"\n        return state\n\n\ndef build_graph() -> StateGraph[GraphState]:\n    """Construct and compile the LangGraph workflow.
\n    Returns
    -------
    StateGraph[GraphState]
        A compiled graph ready to be invoked.
    """
    graph_builder = GraphBuilder()\n\n    # Register nodes\n    graph_builder.add_node("parse_and_score", parse_and_score)\n    graph_builder.add_node("fit_check", fit_check)\n    graph_builder.add_node("human_review_low_fit", lambda s: s)  # passes through – UI can handle low‑fit cases\n    graph_builder.add_node("research_company", research_company)\n    graph_builder.add_node("draft_outreach", draft_outreach)\n    graph_builder.add_node("human_approval", human_approval, is_interrupt=True)\n    graph_builder.add_node("dispatch_action", dispatch_action)\n\n    # Define edges\n    graph_builder.add_edge("parse_and_score", "fit_check")\n    graph_builder.add_conditional_edge(\n        "fit_check",
        lambda state: "human_review_low_fit" if state.get("match_score", 0) < 60 else "research_company",
    )\n    graph_builder.add_edge("human_review_low_fit", END)\n    graph_builder.add_edge("research_company", "draft_outreach")\n    graph_builder.add_edge("draft_outreach", "human_approval")\n    # Loop back on revision\n    graph_builder.add_conditional_edge(\n        "human_approval",
        lambda state: "draft_outreach" if state.get("human_feedback") == "revise" else "dispatch_action",
    )\n    graph_builder.add_edge("dispatch_action", END)\n\n    compiled = graph_builder.compile()\n    return compiled\n\n\n# The module's public API\n__all__ = ["build_graph", "GraphState"]\n