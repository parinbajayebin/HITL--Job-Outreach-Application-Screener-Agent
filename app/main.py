"""app/main.py
FastAPI entry-point for HITL Job Screener.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.graph import GraphState, build_graph
from app.services.persistence import get_checkpointer

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    resume_text: str = Field(..., description="Full text of the candidate resume")
    strengths: List[str] = Field(..., description="List of strengths to highlight")
    city: str = Field(..., description="Target city for the job search")
    role: str = Field(..., description="Desired role/title")
    dream_companies: List[str] = Field(default_factory=list, description="Target companies")


class FeedbackRequest(BaseModel):
    human_feedback: str = Field(..., description="One of: 'approve', 'revise', 'reject'")


app = FastAPI(title="HITL Job Screener")

try:
    from app.api.settings import router as settings_router
    app.include_router(settings_router)
except Exception as exc:
    logger.warning("Could not include settings router: %s", exc)


graph = build_graph()
checkpointer = get_checkpointer()


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/run", response_model=dict)
async def run_screener(req: RunRequest) -> dict:
    try:
        thread_id = str(uuid.uuid4())
        state: GraphState = {
            "thread_id": thread_id,
            "resume_text": req.resume_text,
            "strengths": req.strengths,
            "city": req.city,
            "role": req.role,
            "dream_companies": req.dream_companies,
            "revision_count": 0,
        }
        await checkpointer.save(thread_id, state)
        asyncio.create_task(graph.ainvoke(state))
        return {"thread_id": thread_id}
    except Exception as exc:
        logger.exception("Failed to start screener run: %s", exc)
        raise HTTPException(status_code=500, detail=f"Unable to start process: {exc}")


@app.get("/state/{thread_id}", response_model=dict)
async def get_state(thread_id: str) -> dict:
    try:
        state = await checkpointer.load(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return state
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error retrieving state for %s: %s", thread_id, exc)
        raise HTTPException(status_code=500, detail="State retrieval failure")


@app.post("/feedback/{thread_id}")
async def submit_feedback(thread_id: str, payload: FeedbackRequest = Body(...)) -> JSONResponse:
    try:
        state: Any = await checkpointer.load(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        state["human_feedback"] = payload.human_feedback
        if payload.human_feedback == "revise":
            state["revision_count"] = state.get("revision_count", 0) + 1
        await checkpointer.save(thread_id, state)
        asyncio.create_task(graph.ainvoke(state))
        return JSONResponse(content={"status": "feedback accepted", "thread_id": thread_id})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Feedback handling failed for %s: %s", thread_id, exc)
        raise HTTPException(status_code=500, detail="Unable to process feedback")


__all__ = ["app"]
