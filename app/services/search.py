"""Web search service with Tavily primary and DuckDuckGo fallback.

Provides general search, job-specific search, and company research
capabilities used by the LangGraph agent nodes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────
_TAVILY_DAILY_LIMIT: int = 100
_DEFAULT_MAX_RESULTS: int = 5
_JOB_MAX_RESULTS: int = 10

# Job boards to bias search towards
_JOB_BOARD_SITES: List[str] = [
    "news.ycombinator.com",   # HN Who's Hiring
    "internshala.com",
    "indeed.com",
    "linkedin.com/jobs",
]


class SearchService:
    """Unified web search abstraction over Tavily and DuckDuckGo.

    Tavily is preferred (richer snippets, better relevance) with an
    automatic fallback to DuckDuckGo when the Tavily key is missing
    or the daily quota is exhausted.

    Usage
    -----
    >>> svc = SearchService()
    >>> results = await svc.search("FastAPI deployment best practices")
    >>> jobs = await svc.search_jobs("backend engineer", "Bangalore")
    """

    def __init__(self) -> None:
        """Initialise the service and check for API keys."""
        self._settings: Settings = get_settings()
        self._tavily_key: Optional[str] = self._settings.TAVILY_API_KEY

        # Simple per-day rate tracker for Tavily
        self._tavily_calls_today: int = 0
        self._tavily_day: date = date.today()

    # ── Public API ──────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> List[Dict[str, str]]:
        """Run a general web search.

        Parameters
        ----------
        query:
            Free-text search query.
        max_results:
            Maximum number of results to return.

        Returns
        -------
        list[dict]
            Each dict: ``{"title", "url", "snippet", "source"}``.
        """
        if self._can_use_tavily():
            try:
                return await self._search_tavily(query, max_results)
            except Exception as exc:
                logger.warning("Tavily search failed (%s), falling back to DDG", exc)

        return await self._search_duckduckgo(query, max_results)

    async def search_jobs(
        self,
        role: str,
        city: str,
        companies: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for job postings matching the given role and city.

        Builds smart queries and fans out across multiple job boards.

        Parameters
        ----------
        role:
            Target job title, e.g. ``"backend engineer"``.
        city:
            Target city, e.g. ``"Bangalore"``.
        companies:
            Optional dream-company list to bias the search.

        Returns
        -------
        list[dict]
            Up to ``_JOB_MAX_RESULTS`` results, each containing
            ``{"title", "company", "url", "source", "snippet",
            "posted_date"}``.
        """
        queries = self._build_job_queries(role, city, companies)
        all_results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        for query in queries:
            if len(all_results) >= _JOB_MAX_RESULTS:
                break
            raw = await self.search(query, max_results=5)
            for item in raw:
                url = item.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                all_results.append(
                    {
                        "title": item.get("title", ""),
                        "company": self._extract_company_from_snippet(
                            item.get("snippet", ""),
                            item.get("title", ""),
                        ),
                        "url": url,
                        "source": item.get("source", ""),
                        "snippet": item.get("snippet", ""),
                        "posted_date": None,  # filled by JD parser later
                    }
                )
                if len(all_results) >= _JOB_MAX_RESULTS:
                    break

        logger.info(
            "Job search for '%s' in '%s' returned %d results",
            role,
            city,
            len(all_results),
        )
        return all_results[:_JOB_MAX_RESULTS]

    async def research_company(self, company_name: str) -> Dict[str, Any]:
        """Gather publicly available intelligence about a company.

        Parameters
        ----------
        company_name:
            Company to research (e.g. ``"Zerodha"``).

        Returns
        -------
        dict
            ``{"summary", "tech_stack", "recent_news", "portal_url",
            "linkedin_url", "careers_url"}``.
        """
        queries = [
            f"{company_name} tech stack engineering blog",
            f"{company_name} recent news funding",
            f"{company_name} careers page jobs",
        ]

        # Fan out all three searches concurrently
        raw_results = await asyncio.gather(
            self.search(queries[0], max_results=3),
            self.search(queries[1], max_results=3),
            self.search(queries[2], max_results=3),
        )

        tech_results, news_results, careers_results = raw_results

        # Extract structured info
        tech_stack = self._extract_list_from_snippets(
            tech_results, keywords=["python", "java", "go", "rust", "react",
                                      "node", "kubernetes", "docker", "aws",
                                      "gcp", "azure", "terraform", "kafka",
                                      "postgresql", "redis", "mongodb",
                                      "typescript", "django", "fastapi",
                                      "flask", "spring", "rails", "vue",
                                      "angular", "swift", "kotlin"]
        )

        recent_news = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in news_results
        ]

        # Try to find LinkedIn and careers URLs
        linkedin_url = self._find_url_containing(
            tech_results + news_results + careers_results,
            "linkedin.com",
        )
        careers_url = self._find_url_containing(
            careers_results + tech_results,
            "careers",
        )
        portal_url = self._find_url_containing(
            tech_results + careers_results + news_results,
            company_name.lower().replace(" ", ""),
        )

        summary_parts = [r.get("snippet", "") for r in tech_results[:2]]
        summary = " ".join(summary_parts).strip() or f"No summary found for {company_name}."

        return {
            "summary": summary,
            "tech_stack": tech_stack,
            "recent_news": recent_news,
            "portal_url": portal_url,
            "linkedin_url": linkedin_url,
            "careers_url": careers_url,
        }

    # ── Tavily ──────────────────────────────────────────────────────

    def _can_use_tavily(self) -> bool:
        """Check whether Tavily is available and within daily limits."""
        if not self._tavily_key:
            return False

        # Reset counter on day change
        today = date.today()
        if today != self._tavily_day:
            self._tavily_calls_today = 0
            self._tavily_day = today

        return self._tavily_calls_today < _TAVILY_DAILY_LIMIT

    async def _search_tavily(
        self,
        query: str,
        max_results: int,
    ) -> List[Dict[str, str]]:
        """Execute a Tavily search.

        Parameters
        ----------
        query:
            Search query.
        max_results:
            Max number of results.

        Returns
        -------
        list[dict]
            Normalised search results.
        """
        from tavily import AsyncTavilyClient  # lazy import

        client = AsyncTavilyClient(api_key=self._tavily_key)
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )

        self._tavily_calls_today += 1

        results: List[Dict[str, str]] = []
        for item in response.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "source": "tavily",
                }
            )
        return results

    # ── DuckDuckGo ──────────────────────────────────────────────────

    async def _search_duckduckgo(
        self,
        query: str,
        max_results: int,
    ) -> List[Dict[str, str]]:
        """Execute a DuckDuckGo search (fallback provider).

        Parameters
        ----------
        query:
            Search query.
        max_results:
            Max number of results.

        Returns
        -------
        list[dict]
            Normalised search results.
        """
        from duckduckgo_search import AsyncDDGS  # lazy import

        results: List[Dict[str, str]] = []
        try:
            async with AsyncDDGS() as ddgs:
                raw = await ddgs.atext(
                    keywords=query,
                    max_results=max_results,
                )
                for item in raw:
                    results.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("href", ""),
                            "snippet": item.get("body", ""),
                            "source": "duckduckgo",
                        }
                    )
        except Exception as exc:
            logger.error("DuckDuckGo search failed: %s", exc)
        return results

    # ── Query builders ──────────────────────────────────────────────

    @staticmethod
    def _build_job_queries(
        role: str,
        city: str,
        companies: Optional[List[str]] = None,
    ) -> List[str]:
        """Build a list of search queries targeting job boards.

        Parameters
        ----------
        role:
            Target role name.
        city:
            Target city.
        companies:
            Optional dream-company names.

        Returns
        -------
        list[str]
            2–4 search queries.
        """
        base = f"{role} jobs in {city}"
        queries: List[str] = [base]

        # Add board-specific queries
        for site in _JOB_BOARD_SITES[:2]:
            queries.append(f"site:{site} {base}")

        # Add company-specific query if dream companies provided
        if companies:
            company_str = " OR ".join(f'"{c}"' for c in companies[:5])
            queries.append(f"{base} ({company_str})")

        return queries

    # ── Extraction helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_company_from_snippet(snippet: str, title: str) -> str:
        """Best-effort extraction of company name from title or snippet.

        A simple heuristic: takes the text before " - " or " | " or " at "
        in the title, which is a common pattern on job boards.

        Parameters
        ----------
        snippet:
            Search result snippet.
        title:
            Search result title.

        Returns
        -------
        str
            Extracted company name, or ``"Unknown"``.
        """
        for sep in [" - ", " | ", " at "]:
            if sep in title:
                parts = title.split(sep)
                # Company is usually the last part for " - " and " | "
                # and after " at "
                if sep == " at ":
                    return parts[-1].strip()
                return parts[-1].strip()
        return "Unknown"

    @staticmethod
    def _extract_list_from_snippets(
        results: List[Dict[str, str]],
        keywords: List[str],
    ) -> List[str]:
        """Find known technology keywords mentioned in search snippets.

        Parameters
        ----------
        results:
            Search result dicts.
        keywords:
            Known tech keywords to look for (case-insensitive).

        Returns
        -------
        list[str]
            Deduplicated list of matched keywords.
        """
        combined = " ".join(r.get("snippet", "") for r in results).lower()
        found: List[str] = []
        seen: set[str] = set()
        for kw in keywords:
            if kw in combined and kw not in seen:
                found.append(kw)
                seen.add(kw)
        return found

    @staticmethod
    def _find_url_containing(
        results: List[Dict[str, str]],
        substring: str,
    ) -> str:
        """Return the first URL that contains a given substring.

        Parameters
        ----------
        results:
            Search result dicts.
        substring:
            Substring to look for in the URL.

        Returns
        -------
        str
            Matching URL or empty string.
        """
        for r in results:
            url = r.get("url", "")
            if substring.lower() in url.lower():
                return url
        return ""
