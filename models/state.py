"""Core state schemas for the HITL Job Outreach Agent.

This module defines:
- RequirementProfile: A single city + role + dream-companies profile with
  strength scoring, job matches, company research, and outreach drafts.
- ScreenerState: The LangGraph-compatible TypedDict that flows through
  every node in the state machine.
- UserPreferences: Top-level user record that owns one-or-more profiles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# RequirementProfile — one "search intent" (city × role × companies)
# ---------------------------------------------------------------------------

class RequirementProfile(BaseModel):
    """A single requirement profile representing a user's target role in a
    specific city, with an optional list of dream companies.

    The profile tracks the full lifecycle:
      initialized → scored → jobs_fetched → research_done → drafted →
      awaiting_human → approved | rejected | terminated
    """

    # ---- Identity ----------------------------------------------------------
    profile_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this profile.",
    )

    # ---- Search Parameters -------------------------------------------------
    city: str = Field(..., description="Target city for the job search.")
    role: str = Field(..., description="Target role / job title.")
    dream_companies: List[str] = Field(
        default_factory=list,
        description="Optional shortlist of preferred companies.",
    )

    # ---- Strengths & Resume ------------------------------------------------
    strengths: List[str] = Field(
        default_factory=list,
        description=(
            "User's declared strengths (e.g. ['Kubernetes', 'Python', 'CI/CD']). "
            "These are scored against the resume, NOT against a JD."
        ),
    )
    resume_text: str = Field(
        default="",
        description="Plain-text content of the user's resume.",
    )
    resume_url: Optional[str] = Field(
        default=None,
        description="Supabase storage URL for the uploaded resume PDF.",
    )

    # ---- Strength Scoring --------------------------------------------------
    match_score: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="0-100 score: how well the resume evidences declared strengths.",
    )
    evidenced_strengths: Optional[List[Dict]] = Field(
        default=None,
        description=(
            "List of dicts with keys 'strength', 'evidence', 'score'. "
            "Example: [{'strength': 'K8s', 'evidence': 'Built cluster at XYZ', 'score': 85}]"
        ),
    )
    missing_strengths: Optional[List[str]] = Field(
        default=None,
        description="Strengths the user declared but that are NOT evidenced in the resume.",
    )

    # ---- Job Matches -------------------------------------------------------
    job_matches: List[Dict] = Field(
        default_factory=list,
        description=(
            "Scraped/searched job postings. Each dict has keys: "
            "title, company, url, source, jd_text, relevance_score, posted_date."
        ),
    )

    # ---- Company Research --------------------------------------------------
    company_insights: Optional[str] = Field(
        default=None,
        description="LLM-generated research summary for the target company.",
    )
    company_links: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Discovered links for the company. "
            "Keys: 'portal', 'linkedin', 'careers'."
        ),
    )

    # ---- Outreach Drafts ---------------------------------------------------
    draft_email: Optional[str] = Field(
        default=None,
        description="Generated cold-outreach email body.",
    )
    draft_linkedin: Optional[str] = Field(
        default=None,
        description="Generated LinkedIn connection/InMail note.",
    )

    # ---- HITL Feedback Loop ------------------------------------------------
    human_feedback: Optional[str] = Field(
        default=None,
        description="Free-text feedback from the human reviewer.",
    )
    revision_count: int = Field(
        default=0,
        description="Number of times the draft has been revised after human feedback.",
    )

    # ---- Lifecycle ---------------------------------------------------------
    status: Literal[
        "initialized",
        "scored",
        "jobs_fetched",
        "research_done",
        "drafted",
        "awaiting_human",
        "approved",
        "rejected",
        "terminated",
    ] = Field(
        default="initialized",
        description="Current stage in the profile lifecycle.",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the profile was created.",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last update.",
    )

    class Config:
        """Pydantic model configuration."""

        json_schema_extra = {
            "example": {
                "city": "San Francisco",
                "role": "Senior Backend Engineer",
                "dream_companies": ["Stripe", "Datadog"],
                "strengths": ["Python", "Kubernetes", "CI/CD", "PostgreSQL"],
            }
        }


# ---------------------------------------------------------------------------
# ScreenerState — LangGraph TypedDict state
# ---------------------------------------------------------------------------

class ScreenerState(TypedDict):
    """LangGraph-compatible state dictionary that flows through every node.

    Uses ``TypedDict`` (not Pydantic) because LangGraph requires a plain
    dict-like state object for channel-based updates.

    Attributes:
        thread_id:              Unique conversation / run identifier.
        user_id:                Owner of this screening session.
        profiles:               List of ``RequirementProfile`` dicts (one per
                                city × role combo).
        current_profile_index:  Index into ``profiles`` for the profile
                                currently being processed.
        overall_status:         Aggregate status — ``"running"``,
                                ``"completed"``, or ``"failed"``.
        created_at:             ISO-8601 timestamp string.
        messages:               LangGraph message list for inter-node
                                communication.
    """

    thread_id: str
    user_id: str
    profiles: list  # List[dict] — serialised RequirementProfile objects
    current_profile_index: int
    overall_status: str  # "running" | "completed" | "failed"
    created_at: str  # ISO-8601
    messages: list  # LangGraph message passing


# ---------------------------------------------------------------------------
# UserPreferences — top-level user record
# ---------------------------------------------------------------------------

class UserPreferences(BaseModel):
    """Top-level user record that owns one or more ``RequirementProfile``s.

    Stores identity, contact info, resume data, and all associated
    requirement profiles.
    """

    user_id: str = Field(..., description="Unique user identifier.")
    email: str = Field(..., description="Primary contact email address.")
    linkedin_profile_url: Optional[str] = Field(
        default=None,
        description="URL to the user's LinkedIn profile.",
    )
    full_name: str = Field(..., description="User's full display name.")
    resume_text: str = Field(
        default="",
        description="Plain-text content of the master resume.",
    )
    resume_url: Optional[str] = Field(
        default=None,
        description="Supabase storage URL for the uploaded resume.",
    )
    profiles: List[RequirementProfile] = Field(
        default_factory=list,
        description="All requirement profiles owned by this user.",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the user record was created.",
    )

    class Config:
        """Pydantic model configuration."""

        json_schema_extra = {
            "example": {
                "user_id": "usr_abc123",
                "email": "alice@example.com",
                "full_name": "Alice Smith",
                "linkedin_profile_url": "https://linkedin.com/in/alicesmith",
            }
        }
