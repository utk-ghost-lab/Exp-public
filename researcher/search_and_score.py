"""Shared search & score logic used by both Apply Manager and Research routes.

Centralises title filtering, deduplication, and the search-then-score pipeline
so the two call-sites stay in sync.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Title filter — keep only Senior PM+ roles
# ---------------------------------------------------------------------------

_EXCLUDE_TITLE_WORDS = [
    "associate product manager", "junior product manager", "junior pm",
    "associate pm", "apm", "product analyst", "product coordinator",
    "entry level", "assistant product manager",
    "vp product", "vp of product", "vice president product",
    "cpo", "chief product officer", "svp product", "evp product",
]


def is_senior_pm_role(title: str) -> bool:
    """Return True if *title* looks like a Senior PM+ role (not junior, not VP+)."""
    t = (title or "").lower()
    is_pm = (
        "product manager" in t
        or "product management" in t
        or "product lead" in t
        or "product owner" in t
        or "technical pm" in t
    )
    if not is_pm:
        return False
    for excl in _EXCLUDE_TITLE_WORDS:
        if excl in t:
            return False
    return True


def jd_hash(text: str) -> str:
    """SHA-256 hash of JD text for deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# URL-based dedup helper
# ---------------------------------------------------------------------------

def dedup_jobs(jobs: list[dict]) -> list[dict]:
    """Remove duplicate jobs by URL (or title@company fallback)."""
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        url = job.get("job_url", "")
        if url:
            if url in seen:
                continue
            seen.add(url)
        else:
            key = f"{job.get('title', '')}@{job.get('company', '')}"
            if key in seen:
                continue
            seen.add(key)
        unique.append(job)
    return unique


# ---------------------------------------------------------------------------
# Full search → score pipeline
# ---------------------------------------------------------------------------

def search_and_score(
    date_posted: str = "week",
    num_pages: int = 1,
    min_score: int = 65,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """Search JSearch, filter to Senior PM+, score, return sorted list.

    Returns list of dicts with keys: title, company, location, job_url,
    description, fit_score, recommendation, job_id, description_hash,
    and optionally components, missing_critical_skills, signals, etc.
    """
    from researcher.jsearch_client import search_jobs
    from researcher.job_scorer import load_pkb, _build_candidate_skills, _build_candidate_domains
    from researcher.lightweight_parser import lightweight_parse_jd, score_search_result

    def _notify(msg: str):
        logger.info("[search_and_score] %s", msg)
        if progress_cb:
            progress_cb(msg)

    pkb = load_pkb()
    candidate_skills = _build_candidate_skills(pkb)
    candidate_domains = _build_candidate_domains(pkb)

    search_queries = [
        "Senior Product Manager OR Lead Product Manager OR Principal Product Manager",
        "Group Product Manager OR Director of Product",
    ]
    india_locations = ("India", "Bangalore", "Hyderabad")

    all_jobs: list[dict] = []

    _notify("Searching India locations...")
    for q in search_queries:
        for loc in india_locations:
            for job in search_jobs(query=q, location=loc, num_pages=num_pages,
                                   date_posted=date_posted, remote_only=False, country="in"):
                all_jobs.append(job)

    _notify("Searching global/US locations...")
    for q in search_queries:
        for job in search_jobs(query=q, location="", num_pages=num_pages,
                               date_posted=date_posted, remote_only=False):
            all_jobs.append(job)

    # Dedup by URL
    all_jobs = dedup_jobs(all_jobs)

    # Filter to PM roles
    all_jobs = [j for j in all_jobs if is_senior_pm_role(j.get("title"))]

    raw_count = len(all_jobs)
    _notify(f"Scoring {raw_count} PM roles...")

    # Score
    scored: list[dict] = []
    for job in all_jobs:
        parsed_jd = lightweight_parse_jd(
            description=job.get("description", ""),
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
        )
        score = score_search_result(
            job=job, parsed_jd=parsed_jd, pkb=pkb,
            candidate_skills=candidate_skills,
            candidate_domains=candidate_domains,
        )
        if score["fit_score"] < min_score:
            continue

        job_id = job.get("jsearch_job_id") or job.get("title", "")
        scored.append({
            "title": job.get("title", "Unknown"),
            "company": job.get("company", "Unknown"),
            "location": job.get("location", ""),
            "job_url": job.get("job_url", ""),
            "source": job.get("source", ""),
            "posted_days_ago": job.get("posted_days_ago"),
            "employer_logo": job.get("employer_logo", ""),
            "job_publisher": job.get("job_publisher", ""),
            "description": job.get("description", ""),
            "fit_score": score["fit_score"],
            "recommendation": score["recommendation"],
            "components": score.get("components", {}),
            "missing_critical_skills": score.get("missing_critical_skills", []),
            "signals": parsed_jd.get("signals", {}),
            "job_id": job_id,
            "description_hash": jd_hash(job.get("description", "")),
        })

    scored.sort(key=lambda j: -j["fit_score"])
    _notify(f"Found {len(scored)} jobs scoring {min_score}+")
    return scored
