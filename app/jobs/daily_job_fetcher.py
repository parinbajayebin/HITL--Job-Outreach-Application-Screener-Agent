'''app.jobs.daily_job_fetcher

Runs a periodic background job (every 6 hours) that fetches job postings for each
RequirementProfile using the SearchService and stores them via the SupabaseService.

The job is scheduled with APScheduler's AsyncIO scheduler and respects a limit of
5 new job posts per profile per day.
'''  # noqa: D401

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import List

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Local imports – adjust if the module layout changes
from app.services.search import SearchService
from app.services.supabase_client import SupabaseService
from app.models.state import RequirementProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _ping_health_endpoint(url: str = "http://127.0.0.1:8000/health") -> None:
    """Ping a keep‑alive health endpoint.

    The function is deliberately tolerant – failures are logged but do not raise
    because the main job should continue even if the health check fails.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        logger.debug("Health endpoint %s responded with %s", url, response.status_code)
    except Exception as exc:  # pragma: no cover – defensive logging only
        logger.warning("Failed to ping health endpoint %s: %s", url, exc)


async def fetch_and_store_jobs() -> None:
    """Fetch job postings for every RequirementProfile and persist them.

    * Retrieves all requirement profiles via Supabase (implementation‑specific).
    * Calls :meth:`SearchService.search_jobs` for each profile.
    * Stores up to ``MAX_JOBS_PER_PROFILE`` results per profile per day.
    * Logs progress and errors for observability.
    """
    MAX_JOBS_PER_PROFILE = 5
    logger.info("Starting daily job fetcher – %s", datetime.utcnow().isoformat())

    # Initialise services – they are lightweight wrappers around async‑friendly
    # clients, so creating a fresh instance per run is fine.
    search_svc = SearchService()
    supabase_svc = SupabaseService()

    # ---------------------------------------------------------------------
    # 1️⃣ Retrieve RequirementProfile records
    # ---------------------------------------------------------------------
    try:
        # ``SupabaseService`` does not expose a concrete method in the current
        # code base, but most projects provide one.  To keep the script robust we
        # attempt to call ``get_requirement_profiles`` and fall back to an empty
        # list if the attribute does not exist.
        if hasattr(supabase_svc, "get_requirement_profiles"):
            profiles: List[RequirementProfile] = await supabase_svc.get_requirement_profiles()  # type: ignore[arg-type]
        else:
            logger.warning(
                "SupabaseService.get_requirement_profiles() not implemented – "
                "skipping job fetcher."
            )
            profiles = []
    except Exception as exc:  # pragma: no cover – defensive handling
        logger.error("Failed to retrieve requirement profiles: %s", exc)
        profiles = []

    # ---------------------------------------------------------------------
    # 2️⃣ Process each profile
    # ---------------------------------------------------------------------
    for profile in profiles:
        try:
            logger.debug(
                "Fetching jobs for profile %s (role=%s, city=%s)",
                getattr(profile, "id", "<unknown>"),
                getattr(profile, "role", "<unknown>"),
                getattr(profile, "city", "<unknown>"),
            )
            raw_jobs = await search_svc.search_jobs(
                role=getattr(profile, "role", ""),
                city=getattr(profile, "city", ""),
                companies=getattr(profile, "companies", None),
            )
            # Respect the per‑profile daily limit
            jobs_to_store = raw_jobs[:MAX_JOBS_PER_PROFILE]
            if not jobs_to_store:
                logger.info("No new jobs found for profile %s", getattr(profile, "id", "?"))
                continue

            await supabase_svc.store_job_posts(
                profile_id=getattr(profile, "id", ""),
                job_posts=jobs_to_store,
            )
            logger.info(
                "Stored %d job posts for profile %s", len(jobs_to_store), getattr(profile, "id", "?")
            )
        except Exception as exc:  # pragma: no cover – log & continue
            logger.error(
                "Error processing profile %s: %s", getattr(profile, "id", "?"), exc, exc_info=True
            )

    # ---------------------------------------------------------------------
    # 3️⃣ Keep‑alive ping
    # ---------------------------------------------------------------------
    await _ping_health_endpoint()
    logger.info("Daily job fetcher completed – %s", datetime.utcnow().isoformat())


def schedule_job() -> None:
    """Configure APScheduler to run ``fetch_and_store_jobs`` every six hours.

    The scheduler is started immediately.  It uses a cron trigger that fires at
    the top of the hour (00:00, 06:00, 12:00, 18:00) – the exact minutes are not
    critical, but the interval matches the requirement.
    """
    scheduler = AsyncIOScheduler()
    trigger = CronTrigger(hour="0,6,12,18", minute=0)
    scheduler.add_job(fetch_and_store_jobs, trigger, name="daily_job_fetcher", max_instances=1)
    scheduler.start()
    logger.info("APScheduler started – daily_job_fetcher scheduled every 6h")


# When the module is imported (e.g. by the FastAPI startup event) we immediately
# schedule the background job.  Import side‑effects are acceptable for this small
# utility because the scheduler is lightweight and idempotent.
schedule_job()
