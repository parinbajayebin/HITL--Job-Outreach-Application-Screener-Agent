'''app/main.py
=================
FastAPI entry‑point that wires the LangGraph workflow into HTTP endpoints.

The module performs the following:

* Instantiates a FastAPI app and includes the generic ``app.api`` router (if it
  exists in the project).
* Builds a singleton LangGraph workflow via ``build_graph``.
* Exposes three endpoints:
  - ``POST /run`` – Starts a new screening workflow for a given resume and
    requirement profile.
  - ``GET /state/{thread_id}`` – Retrieves the persisted state from the
    ``SqliteSaver`` checkpoint.
  - ``POST /feedback/{thread_id}`` – Supplies human feedback (approve/revise/
    reject) to an interrupted graph execution.

All endpoints are asynchronous, use proper validation via Pydantic models and
handle unexpected errors gracefully.
'''\n\nfrom __future__ import annotations\n\nimport logging\nimport uuid\nfrom typing import List\n\nfrom fastapi import FastAPI, APIRouter, HTTPException, Depends, Body\nfrom fastapi.responses import JSONResponse\nfrom pydantic import BaseModel, Field\n\nfrom app.services.graph import build_graph, GraphState\nfrom app.services.persistence import SqliteSaver, get_checkpointer\nfrom app.models.profile import RequirementProfile\n\nlogger = logging.getLogger(__name__)\n\n# ---------------------------------------------------------------------------\n# Pydantic request/response models
# ---------------------------------------------------------------------------\n\nclass RunRequest(BaseModel):\n    """Payload for ``POST /run`` that starts a new screening run."""
\n    resume_text: str = Field(..., description="Full text of the candidate's résumé")\n    strengths: List[str] = Field(..., description="List of strengths the user wants to highlight")\n    city: str = Field(..., description="Target city for the job search")\n    role: str = Field(..., description="Desired role/title")\n    dream_companies: List[str] = Field(..., description="Companies the user wishes to target")\n\nclass FeedbackRequest(BaseModel):\n    """Payload for ``POST /feedback/{thread_id}``."""
\n    human_feedback: str = Field(..., description="One of: 'approve', 'revise', 'reject'")\n\n# ---------------------------------------------------------------------------\n# Application setup
# ---------------------------------------------------------------------------\n\napp = FastAPI(title="HITL Job Screener")\n\n# Include the generic API router if present – silence the import error when the module
# does not exist in a minimal scaffold.
try:\n    from app.api import router as api_router  # type: ignore\n    app.include_router(api_router)\nexcept Exception as exc:  # pragma: no cover – optional component\n    logger.warning("app.api router not found or failed to import: %s", exc)\n\n# Build the singleton graph and the SqliteSaver checkpoint manager\ngraph = build_graph()\ncheckpointer = get_checkpointer()  # Expected to return a SqliteSaver instance\n\n# ---------------------------------------------------------------------------\n# Helper utilities
# ---------------------------------------------------------------------------\n\ndef _create_initial_state(req: RunRequest) -> GraphState:\n    """Create the initial LangGraph state for a new run.
\n    A fresh ``thread_id`` (UUID4) is generated and the supplied request data is
    stored. The ``RequirementProfile`` object is also persisted inside the state
    for downstream nodes to reference.
    """
    thread_id = str(uuid.uuid4())\n    profile = RequirementProfile(
        thread_id=thread_id,
        resume_text=req.resume_text,
        strengths=req.strengths,
        city=req.city,
        role=req.role,
        dream_companies=req.dream_companies,
    )\n    # The graph expects a plain dict, not a Pydantic model.
    return {\n        "thread_id": thread_id,
        "resume_text": req.resume_text,
        "strengths": req.strengths,
        "city": req.city,
        "role": req.role,
        "dream_companies": req.dream_companies,
        "profile": profile.dict(),  # optional – downstream nodes may read it\n    }\n\n# ---------------------------------------------------------------------------\n# Endpoint implementations
# ---------------------------------------------------------------------------\n\n@app.post("/run", response_model=dict)\nasync def run_screener(req: RunRequest) -> dict:\n    """Start a new screening workflow.
\n    The function creates a fresh ``GraphState`` and kicks off the LangGraph
    execution in a background ``asyncio`` task so that the HTTP request returns
    immediately with the generated ``thread_id``.
    """
    try:\n        state = _create_initial_state(req)\n        # Persist the initial state – useful for ``GET /state`` before any
        # node has run.
        await checkpointer.save(state["thread_id"], state)\n        # Launch the graph asynchronously – the task runs for the lifetime of
        # the process. Errors are logged inside node implementations.
        import asyncio\n        asyncio.create_task(graph.ainvoke(state))  # type: ignore[attr-defined]\n        return {"thread_id": state["thread_id"]}\n    except Exception as exc:  # pragma: no cover\n        logger.exception("Failed to start screener run: %s", exc)\n        raise HTTPException(status_code=500, detail="Unable to start process")\n\n@app.get("/state/{thread_id}", response_model=dict)\nasync def get_state(thread_id: str) -> dict:\n    """Fetch the current workflow state from the SQLite checkpoint store."""
    try:\n        state = await checkpointer.load(thread_id)\n        if state is None:\n            raise HTTPException(status_code=404, detail="Thread not found")\n        return state  # type: ignore[return-value]\n    except Exception as exc:  # pragma: no cover\n        logger.exception("Error retrieving state for %s: %s", thread_id, exc)\n        raise HTTPException(status_code=500, detail="State retrieval failure")\n\n@app.post("/feedback/{thread_id}")\nasync def submit_feedback(thread_id: str, payload: FeedbackRequest = Body(...)) -> JSONResponse:\n    """Provide human feedback to an interrupted workflow.
\n    The endpoint updates the persisted state with ``human_feedback`` and then
    resumes graph execution. The response contains the updated state after the
    resume step has completed.
    """
    try:\n        state: GraphState | None = await checkpointer.load(thread_id)\n        if state is None:\n            raise HTTPException(status_code=404, detail="Thread not found")\n        state["human_feedback"] = payload.human_feedback\n        # Save the updated state before resuming – the graph may read it
        await checkpointer.save(thread_id, state)\n        import asyncio\n        asyncio.create_task(graph.ainvoke(state))  # type: ignore[attr-defined]\n        return JSONResponse(content={"status": "feedback accepted", "thread_id": thread_id})\n    except Exception as exc:  # pragma: no cover\n        logger.exception("Feedback handling failed for %s: %s", thread_id, exc)\n        raise HTTPException(status_code=500, detail="Unable to process feedback")\n\n# ---------------------------------------------------------------------------\n# Export the FastAPI app for external runners (uvicorn, etc.)\n# ---------------------------------------------------------------------------\n\n__all__ = ["app"]\n