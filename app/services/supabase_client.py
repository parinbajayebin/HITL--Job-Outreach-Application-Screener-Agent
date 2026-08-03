"""Supabase client for resume storage and job post persistence.

Wraps the ``supabase-py`` client to provide domain-specific helpers
for resume upload/download and job-post / outreach CRUD operations.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import Client as SupabaseClient
from supabase import create_client

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# ── Default table names ────────────────────────────────────────────────
_JOB_POSTS_TABLE: str = "job_posts"
_OUTREACH_TABLE: str = "outreach"


class SupabaseService:
    """High-level async-friendly wrapper around the Supabase Python client.

    Responsibilities
    ----------------
    * **Resume storage**: upload / download files to/from a Supabase
      Storage bucket.
    * **Job post persistence**: upsert and query rows in the
      ``job_posts`` table.
    * **Outreach persistence**: store approved outreach records.

    Usage
    -----
    >>> svc = SupabaseService()
    >>> url = await svc.upload_resume(pdf_bytes, "resume.pdf", "user-123")
    >>> jobs = await svc.get_job_posts("profile-456", limit=5)
    """

    def __init__(self) -> None:
        """Create the Supabase client from application settings."""
        self._settings: Settings = get_settings()
        self._client: SupabaseClient = self._build_client()
        self._bucket: str = self._settings.SUPABASE_BUCKET
        self._job_table: str = self._settings.SUPABASE_TABLE or _JOB_POSTS_TABLE

    # ── Resume Storage ──────────────────────────────────────────────

    async def upload_resume(
        self,
        file_bytes: bytes,
        filename: str,
        user_id: str,
    ) -> str:
        """Upload a resume file to Supabase Storage.

        The file is stored under ``{user_id}/{filename}`` inside the
        configured bucket.  If the bucket does not exist it is created
        automatically (as a *private* bucket by default — public URLs
        are generated via signed or public access as configured in the
        Supabase dashboard).

        Parameters
        ----------
        file_bytes:
            Raw bytes of the resume file.
        filename:
            Original filename (used as the object name).
        user_id:
            Owning user identifier (used as the folder prefix).

        Returns
        -------
        str
            The public URL of the uploaded file.
        """
        self._ensure_bucket()
        path = f"{user_id}/{filename}"

        try:
            # Remove existing file if present (upsert semantics)
            try:
                self._client.storage.from_(self._bucket).remove([path])
            except Exception:
                pass  # file may not exist — that's fine

            self._client.storage.from_(self._bucket).upload(
                path=path,
                file=file_bytes,
                file_options={"content-type": "application/octet-stream"},
            )
            logger.info("Uploaded resume to %s/%s", self._bucket, path)
        except Exception as exc:
            logger.error("Failed to upload resume: %s", exc)
            raise RuntimeError(
                f"Resume upload failed for {filename}: {exc}"
            ) from exc

        # Build the public URL
        public_url: str = self._client.storage.from_(
            self._bucket
        ).get_public_url(path)
        return public_url

    async def get_resume(self, user_id: str, filename: str) -> bytes:
        """Download a resume from Supabase Storage.

        Parameters
        ----------
        user_id:
            Owning user identifier.
        filename:
            Filename to download.

        Returns
        -------
        bytes
            Raw file bytes.

        Raises
        ------
        RuntimeError
            If the download fails.
        """
        path = f"{user_id}/{filename}"
        try:
            data = self._client.storage.from_(self._bucket).download(path)
            logger.info("Downloaded resume %s/%s (%d bytes)", self._bucket, path, len(data))
            return data
        except Exception as exc:
            logger.error("Failed to download resume %s: %s", path, exc)
            raise RuntimeError(
                f"Resume download failed for {path}: {exc}"
            ) from exc

    # ── Job Posts ────────────────────────────────────────────────────

    async def store_job_posts(
        self,
        jobs: List[Dict[str, Any]],
        profile_id: str,
    ) -> None:
        """Upsert a batch of job posts into the ``job_posts`` table.

        Each row is keyed by its ``url`` (deduplicated via upsert).

        Parameters
        ----------
        jobs:
            List of job-post dicts. Expected keys:
            ``title, company, url, source, jd_text, relevance_score,
            posted_date``.
        profile_id:
            The requirement-profile that owns these jobs.
        """
        if not jobs:
            return

        rows: List[Dict[str, Any]] = []
        for job in jobs:
            rows.append(
                {
                    "id": job.get("id", str(uuid.uuid4())),
                    "profile_id": profile_id,
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "url": job.get("url", ""),
                    "source": job.get("source", ""),
                    "jd_text": job.get("jd_text", ""),
                    "relevance_score": job.get("relevance_score", 0.0),
                    "posted_date": job.get("posted_date"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        try:
            self._client.table(self._job_table).upsert(
                rows,
                on_conflict="url",
            ).execute()
            logger.info(
                "Upserted %d job posts for profile %s",
                len(rows),
                profile_id,
            )
        except Exception as exc:
            logger.error("Failed to upsert job posts: %s", exc)
            raise RuntimeError(
                f"Job post storage failed: {exc}"
            ) from exc

    async def get_job_posts(
        self,
        profile_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve the top job posts for a profile ordered by relevance.

        Parameters
        ----------
        profile_id:
            The requirement-profile to query.
        limit:
            Maximum number of rows to return.

        Returns
        -------
        list[dict]
            Job post records.
        """
        try:
            response = (
                self._client.table(self._job_table)
                .select("*")
                .eq("profile_id", profile_id)
                .order("relevance_score", desc=True)
                .limit(limit)
                .execute()
            )
            return response.data or []
        except Exception as exc:
            logger.error("Failed to fetch job posts: %s", exc)
            return []

    # ── Outreach ────────────────────────────────────────────────────

    async def store_outreach(
        self,
        outreach: Dict[str, Any],
        profile_id: str,
    ) -> None:
        """Persist an approved outreach record.

        Parameters
        ----------
        outreach:
            Outreach data dict (channel, recipient, subject, body, etc.).
        profile_id:
            The owning requirement-profile.
        """
        row: Dict[str, Any] = {
            "id": outreach.get("id", str(uuid.uuid4())),
            "profile_id": profile_id,
            "channel": outreach.get("channel", ""),
            "recipient": outreach.get("recipient", ""),
            "subject": outreach.get("subject", ""),
            "body": outreach.get("body", ""),
            "status": outreach.get("status", "approved"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self._client.table(_OUTREACH_TABLE).insert(row).execute()
            logger.info("Stored outreach %s for profile %s", row["id"], profile_id)
        except Exception as exc:
            logger.error("Failed to store outreach: %s", exc)
            raise RuntimeError(
                f"Outreach storage failed: {exc}"
            ) from exc

    # ── Private helpers ─────────────────────────────────────────────

    def _build_client(self) -> SupabaseClient:
        """Construct the Supabase client from settings.

        Returns
        -------
        SupabaseClient
            Initialised supabase client.

        Raises
        ------
        ValueError
            If required Supabase credentials are missing.
        """
        url = self._settings.SUPABASE_URL
        key = self._settings.SUPABASE_KEY

        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in the environment."
            )

        return create_client(url, key)

    def _ensure_bucket(self) -> None:
        """Create the storage bucket if it does not already exist.

        Uses a *private* bucket by default.  Swallows
        ``already exists`` errors gracefully.
        """
        try:
            self._client.storage.create_bucket(
                self._bucket,
                options={"public": False},
            )
            logger.info("Created storage bucket '%s'", self._bucket)
        except Exception as exc:
            # Bucket likely already exists — that's fine
            if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower():
                logger.debug("Bucket '%s' already exists", self._bucket)
            else:
                # Log but don't crash — the bucket may already exist under
                # a different error message format depending on Supabase version
                logger.warning(
                    "Bucket creation returned an error (may already exist): %s",
                    exc,
                )

    def _ensure_table(self, table_name: str) -> None:
        """Create a table via Supabase RPC if it does not already exist.

        .. note::
           This requires a ``create_table_if_not_exists`` PostgreSQL
           function to be deployed in your Supabase project.  The
           function should accept ``table_name TEXT`` and create the
           appropriate schema.

        Parameters
        ----------
        table_name:
            Name of the table to ensure.
        """
        try:
            self._client.rpc(
                "create_table_if_not_exists",
                {"p_table_name": table_name},
            ).execute()
            logger.info("Ensured table '%s' exists via RPC", table_name)
        except Exception as exc:
            logger.warning(
                "RPC create_table_if_not_exists failed for '%s': %s. "
                "Ensure the table is created manually or via migrations.",
                table_name,
                exc,
            )
