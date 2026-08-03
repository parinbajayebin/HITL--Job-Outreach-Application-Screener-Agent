"""Free-tier LLM cascade wrapper.

Tries providers in order: OpenAI gpt-4o-mini → falls back gracefully.
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

# ── Constants ───────────────────────────────────────────────────────────
_DEFAULT_MODEL: str = "gpt-4o-mini"
_MAX_RETRIES: int = 3
_TIMEOUT_SECONDS: int = 30
_BACKOFF_BASE: float = 2.0  # exponential backoff base in seconds


class LLMProvider:
    """Thin async wrapper around the OpenAI chat-completions API.

    Features
    --------
    * Structured output via Pydantic ``output_schema`` (uses JSON-mode
      response_format with a json_schema directive).
    * Automatic retry with exponential backoff (up to ``_MAX_RETRIES``).
    * 30-second per-call timeout.

    Usage
    -----
    >>> provider = LLMProvider()
    >>> text = await provider.invoke("Summarise this JD …")
    >>> parsed = await provider.invoke("Score …", output_schema=ScoreResult)
    """

    def __init__(self) -> None:
        """Initialise the provider by reading API keys from config."""
        self._settings: Settings = get_settings()
        self._client: AsyncOpenAI = self._build_openai_client()

    # ── Public API ──────────────────────────────────────────────────

    async def invoke(
        self,
        prompt: str,
        system_prompt: str = "",
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        """Send a single user prompt (with optional system message) to the LLM.

        Parameters
        ----------
        prompt:
            The user-facing prompt text.
        system_prompt:
            Optional system-level instruction prepended to the conversation.
        output_schema:
            If provided, the LLM response is parsed into this Pydantic model
            using OpenAI's structured-output / JSON-mode.

        Returns
        -------
        str | BaseModel
            Raw assistant text **or** a validated Pydantic instance when
            ``output_schema`` is supplied.
        """
        messages: List[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return await self.invoke_with_messages(messages, output_schema=output_schema)

    async def invoke_with_messages(
        self,
        messages: List[dict[str, Any]],
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        """Send an arbitrary messages list and optionally parse structured output.

        Parameters
        ----------
        messages:
            OpenAI-style ``[{"role": …, "content": …}, …]`` list.
        output_schema:
            Pydantic model class for structured output parsing.

        Returns
        -------
        str | BaseModel
            Raw text or a validated Pydantic instance.

        Raises
        ------
        RuntimeError
            When all retry attempts are exhausted.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await self._call_openai(messages, output_schema)
            except RateLimitError as exc:
                # Rate-limited — wait longer before retrying
                last_error = exc
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "OpenAI rate-limit hit (attempt %d/%d). "
                    "Retrying in %.1fs …",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            except APITimeoutError as exc:
                last_error = exc
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "OpenAI timeout (attempt %d/%d). Retrying in %.1fs …",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            except APIConnectionError as exc:
                last_error = exc
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "OpenAI connection error (attempt %d/%d). "
                    "Retrying in %.1fs …",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            except AuthenticationError as exc:
                # Auth errors are non-retriable — surface immediately
                logger.error("OpenAI authentication failed: %s", exc)
                raise RuntimeError(
                    "OpenAI authentication failed. Check your OPENAI_API_KEY."
                ) from exc

        # All attempts exhausted
        raise RuntimeError(
            f"LLM call failed after {_MAX_RETRIES} retries: {last_error}"
        ) from last_error

    # ── Private helpers ─────────────────────────────────────────────

    def _build_openai_client(self) -> AsyncOpenAI:
        """Construct the ``AsyncOpenAI`` client from settings."""
        if not self._settings.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is required but not set in the environment."
            )
        return AsyncOpenAI(
            api_key=self._settings.OPENAI_API_KEY,
            timeout=_TIMEOUT_SECONDS,
        )

    async def _call_openai(
        self,
        messages: List[dict[str, Any]],
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Union[str, BaseModel]:
        """Execute a single OpenAI chat-completion request.

        When ``output_schema`` is provided the request uses JSON-mode with
        a ``json_schema`` response-format so that the model returns
        strictly-typed output.

        Parameters
        ----------
        messages:
            Chat messages in OpenAI format.
        output_schema:
            Optional Pydantic model for structured output.

        Returns
        -------
        str | BaseModel
            Parsed model or raw text.
        """
        kwargs: dict[str, Any] = {
            "model": _DEFAULT_MODEL,
            "messages": messages,
        }

        if output_schema is not None:
            # Build a JSON-Schema response format for structured output.
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
            return self._parse_structured_output(raw_content, output_schema)

        return raw_content

    @staticmethod
    def _parse_structured_output(
        raw: str,
        schema: Type[BaseModel],
    ) -> BaseModel:
        """Parse and validate a JSON string into the given Pydantic model.

        Parameters
        ----------
        raw:
            Raw JSON string from the LLM response.
        schema:
            Target Pydantic model class.

        Returns
        -------
        BaseModel
            A validated instance of ``schema``.

        Raises
        ------
        ValueError
            If the response is not valid JSON or fails Pydantic validation.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", raw[:500])
            raise ValueError(
                f"LLM response is not valid JSON: {raw[:200]}"
            ) from exc

        try:
            return schema.model_validate(data)
        except Exception as exc:
            logger.error(
                "LLM JSON does not match schema %s: %s",
                schema.__name__,
                raw[:500],
            )
            raise ValueError(
                f"LLM response does not match {schema.__name__}: {exc}"
            ) from exc
