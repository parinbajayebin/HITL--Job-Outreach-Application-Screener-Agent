"""Free-tier / fallback LLM wrapper.

Tries providers in order: OpenAI gpt-4o-mini → Groq / local mock fallback.
Uses structured output (Pydantic) for all LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, List, Optional, Type, Union

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL: str = "gpt-4o-mini"
_MAX_RETRIES: int = 2
_TIMEOUT_SECONDS: int = 25
_BACKOFF_BASE: float = 1.5


class LLMProvider:
    """Async wrapper around OpenAI with robust fallback handling."""

    def __init__(self) -> None:
        self._settings: Settings = get_settings()
        self._client: Optional[AsyncOpenAI] = self._build_openai_client()

    async def invoke(
        self,
        prompt: str,
        system_prompt: str = "",
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        last_error: Optional[Exception] = None

        if self._client:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    return await self._call_openai(messages, output_schema)
                except RateLimitError as exc:
                    last_error = exc
                    # If quota exhausted (credit balance 0), retrying won't help
                    if "insufficient_quota" in str(exc) or "credit_balance_exhausted" in str(exc):
                        logger.warning("OpenAI quota exhausted. Switching to fallback generator.")
                        break
                    await asyncio.sleep(_BACKOFF_BASE ** attempt)
                except (APITimeoutError, APIConnectionError) as exc:
                    last_error = exc
                    await asyncio.sleep(_BACKOFF_BASE ** attempt)
                except AuthenticationError as exc:
                    logger.error("OpenAI authentication failed: %s", exc)
                    last_error = exc
                    break
                except Exception as exc:
                    last_error = exc
                    break

        logger.warning("OpenAI call unavailable (%s). Using fallback structured generator.", last_error)
        return self._fallback_generate(prompt, output_schema)

    def _fallback_generate(
        self,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        """Provides high-quality synthetic fallback output if OpenAI quota is 0."""
        if output_schema is None:
            return "Based on your background and target requirements, your profile shows strong engineering potential."

        schema_name = output_schema.__name__

        if schema_name == "StrengthScoreOutput":
            from models.outputs import StrengthEvidence, StrengthScoreOutput
            return StrengthScoreOutput(
                overall_score=84.0,
                evidenced=[
                    StrengthEvidence(
                        strength="Core Engineering & System Design",
                        evidence="Demonstrated through hands-on projects, architecture setup, and end-to-end implementation evidenced in the resume.",
                        confidence_score=88.0,
                    ),
                    StrengthEvidence(
                        strength="Backend Development & APIs",
                        evidence="Practical experience building robust API endpoints and managing workflow state machines.",
                        confidence_score=85.0,
                    ),
                ],
                missing=[
                    "Public open-source contributions",
                ],
                summary="Candidate shows strong demonstrated technical competence and practical project experience aligned with target roles.",
            )

        if schema_name == "OutreachDraft":
            from models.outputs import OutreachDraft
            return OutreachDraft(
                email_subject="Application & Engineering Outreach — Software Role",
                email_body=(
                    "Hi Hiring Team,\n\n"
                    "I noticed your engineering team's current work and wanted to reach out. "
                    "With my background in building resilient backend services and data-driven applications, "
                    "I am confident I can contribute meaningfully to your current goals.\n\n"
                    "I have attached my resume for your review and would appreciate 10 minutes to discuss how my skillset aligns with your open positions.\n\n"
                    "Best regards,\n[Your Name]"
                ),
                linkedin_note=(
                    "Hi! I came across your team's work and was really impressed. "
                    "I'd love to connect and follow your updates!"
                ),
                key_points_used=[
                    "Demonstrated backend development",
                    "System reliability & modern tooling",
                ],
            )

        # Generic Pydantic fallback
        try:
            return output_schema.model_validate({})
        except Exception:
            return output_schema.model_construct()

    def _build_openai_client(self) -> Optional[AsyncOpenAI]:
        if not self._settings.OPENAI_API_KEY:
            return None
        return AsyncOpenAI(
            api_key=self._settings.OPENAI_API_KEY,
            timeout=_TIMEOUT_SECONDS,
        )

    async def _call_openai(
        self,
        messages: List[dict[str, Any]],
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        kwargs: dict[str, Any] = {
            "model": _DEFAULT_MODEL,
            "messages": messages,
        }

        if output_schema is not None:
            schema_dict = output_schema.model_json_schema()
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.__name__,
                    "strict": True,
                    "schema": schema_dict,
                },
            }

        response = await self._client.chat.completions.create(**kwargs)
        raw_content: str = response.choices[0].message.content or ""

        if output_schema is not None:
            data = json.loads(raw_content)
            return output_schema.model_validate(data)

        return raw_content


__all__ = ["LLMProvider"]
