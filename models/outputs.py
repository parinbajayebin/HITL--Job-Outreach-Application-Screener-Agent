"""Structured LLM output schemas for the HITL Job Outreach Agent.

These Pydantic models are used as ``response_format`` / structured-output
targets when calling the LLM, ensuring every generation is machine-parseable
and validated before entering the pipeline.

Models:
- StrengthEvidence:    A single strength ↔ resume-evidence pair with score.
- StrengthScoreOutput: Aggregated strength-scoring result for a resume.
- JobRelevanceScore:   How relevant a scraped JD is to the user's strengths.
- CompanyResearch:     Structured company intel gathered from web search.
- OutreachDraft:       Email + LinkedIn outreach copy ready for human review.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Strength Scoring
# ---------------------------------------------------------------------------

class StrengthEvidence(BaseModel):
    """Maps a single declared strength to concrete evidence found in the
    user's resume, along with a confidence score.

    Example::

        StrengthEvidence(
            strength="Kubernetes",
            evidence="Designed and managed a 50-node K8s cluster at Acme Corp",
            confidence_score=92.0,
        )
    """

    strength: str = Field(
        ...,
        description="The declared strength being evaluated.",
    )
    evidence: str = Field(
        ...,
        description=(
            "The specific project, experience, or achievement from the "
            "resume that demonstrates this strength."
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0,
        le=100,
        description="0-100 confidence that the resume genuinely evidences this strength.",
    )


class StrengthScoreOutput(BaseModel):
    """Complete result of scoring a resume against the user's declared
    strengths list.

    The LLM returns this as structured output so the pipeline can
    programmatically route profiles with low scores to a feedback loop.
    """

    overall_score: float = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "Overall match score: how well the resume evidences the "
            "declared strengths (0 = no evidence, 100 = perfectly evidenced)."
        ),
    )
    evidenced: List[StrengthEvidence] = Field(
        ...,
        description="Strengths that ARE backed by concrete resume evidence.",
    )
    missing: List[str] = Field(
        ...,
        description="Strengths declared by the user but NOT evidenced in the resume.",
    )
    summary: str = Field(
        ...,
        description="Brief overall assessment of resume–strengths alignment.",
    )


# ---------------------------------------------------------------------------
# Job Relevance
# ---------------------------------------------------------------------------

class JobRelevanceScore(BaseModel):
    """Scores how relevant a specific job posting (JD) is to the user's
    declared strengths.

    Used after scraping to rank and filter jobs before presenting them.
    """

    relevance_score: float = Field(
        ...,
        ge=0,
        le=100,
        description="0-100 relevance of the JD to the user's strength list.",
    )
    matching_strengths: List[str] = Field(
        ...,
        description="Which of the user's declared strengths are mentioned or implied in the JD.",
    )
    reason: str = Field(
        ...,
        description="Short explanation of why this score was assigned.",
    )


# ---------------------------------------------------------------------------
# Company Research
# ---------------------------------------------------------------------------

class CompanyResearch(BaseModel):
    """Structured intelligence about a target company, gathered via
    web search (Tavily / DuckDuckGo) and summarised by the LLM.
    """

    company_name: str = Field(
        ...,
        description="Official name of the company.",
    )
    summary: str = Field(
        ...,
        description="2-3 sentence overview of the company and what it does.",
    )
    tech_stack: List[str] = Field(
        default_factory=list,
        description="Known technologies used by the company's engineering team.",
    )
    recent_news: List[str] = Field(
        default_factory=list,
        description="Notable recent news items (funding rounds, launches, etc.).",
    )
    engineering_focus: str = Field(
        default="",
        description="What the engineering org is primarily working on.",
    )
    portal_url: Optional[str] = Field(
        default=None,
        description="URL to the company's main job portal.",
    )
    linkedin_url: Optional[str] = Field(
        default=None,
        description="URL to the company's LinkedIn page.",
    )
    careers_url: Optional[str] = Field(
        default=None,
        description="URL to the company's careers / jobs page.",
    )


# ---------------------------------------------------------------------------
# Outreach Draft
# ---------------------------------------------------------------------------

class OutreachDraft(BaseModel):
    """Generated outreach copy for email and LinkedIn, ready for
    human-in-the-loop review before dispatch.

    The ``key_points_used`` field lets the reviewer see which strengths
    and projects the LLM chose to highlight, making feedback faster.
    """

    email_subject: str = Field(
        ...,
        description="Subject line for the cold-outreach email.",
    )
    email_body: str = Field(
        ...,
        max_length=2000,
        description="Full email body (plain text, ≤ 2 000 chars).",
    )
    linkedin_note: str = Field(
        ...,
        max_length=1000,
        description="LinkedIn connection request or InMail note (≤ 1 000 chars).",
    )
    key_points_used: List[str] = Field(
        ...,
        description=(
            "Which strengths, projects, or achievements from the resume "
            "were highlighted in the outreach copy."
        ),
    )
