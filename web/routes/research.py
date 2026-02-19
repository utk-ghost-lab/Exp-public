"""Search jobs routes: filter form + JSearch API + lightweight scoring."""

import json
import logging
import sys
import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import state as web_state
from web.resume_store import load_generated_resumes, load_applied_jobs, add_applied_job
from web.research_persistence import save_last_search, load_last_search

logger = logging.getLogger(__name__)

router = APIRouter()

def _load_defaults() -> dict:
    """Minimal defaults — only date and pages are used."""
    return {}


@router.get("", response_class=HTMLResponse)
async def get_research_page(request: Request):
    """Search page with filter panel and results area."""
    templates = request.app.state.templates
    defaults = _load_defaults()
    return templates.TemplateResponse("research.html", {
        "request": request,
        "defaults": defaults,
    })


@router.get("/results/{search_id}", response_class=HTMLResponse)
async def get_search_results(request: Request, search_id: str):
    """Restore search results by search_id (for back-navigation). Loads from disk if not in memory."""
    templates = request.app.state.templates
    store = web_state.research_stores.get(search_id)
    if not store:
        # Try loading from persisted file (survives server restarts)
        persisted = load_last_search()
        if persisted and persisted.get("search_id") == search_id:
            store = persisted
            web_state.research_stores[search_id] = store
        else:
            return HTMLResponse(
                '<p class="text-gray-500 text-sm py-4">Search results expired. Please run a new search.</p>',
                status_code=200,
            )
    jobs = store.get("jobs", [])
    stats = dict(store.get("stats", {}))
    hide_applied = request.query_params.get("hide_applied")
    if hide_applied is None:
        hide_applied = store.get("hide_applied", False)
    else:
        hide_applied = hide_applied == "1"
    if "publisher_counts" not in stats and jobs:
        stats["publisher_counts"] = dict(Counter(
            (j.get("job_publisher") or j.get("source") or "Unknown") for j in jobs
        ))
    # Re-populate search_job_descriptions so Generate Resume works
    for j in jobs:
        jid = j.get("job_id")
        desc = j.get("description", "")
        if jid and desc:
            web_state.search_job_descriptions[jid] = desc
    # Load generated resumes and applied jobs for UI
    generated = load_generated_resumes()
    jobs_with_resumes = set(generated.keys())
    applied_job_ids = load_applied_jobs()
    if hide_applied:
        jobs = [j for j in jobs if j.get("job_id") not in applied_job_ids]
        stats = dict(stats)
        stats["total"] = len(jobs)
    show_loaded_banner = request.query_params.get("restored") == "1"
    return templates.TemplateResponse(
        "partials/search_results.html",
        {
            "request": request,
            "jobs": jobs,
            "stats": stats,
            "search_id": search_id,
            "jobs_with_resumes": jobs_with_resumes,
            "applied_job_ids": applied_job_ids,
            "hide_applied": hide_applied,
            "show_loaded_banner": show_loaded_banner,
        },
    )


@router.post("/applied", response_class=JSONResponse)
async def post_applied(request: Request):
    """Mark a job as applied (idempotent)."""
    try:
        body = await request.json()
        job_id = (body.get("job_id") or "").strip()
        if not job_id:
            return JSONResponse({"detail": "job_id required"}, status_code=400)
        add_applied_job(job_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.exception("post_applied failed: %s", e)
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.post("/search", response_class=HTMLResponse)
async def post_search(request: Request):
    """HTMX endpoint: search JSearch API, score results, return HTML partial.

    Single query for Product Manager (all locations). Filters to PM roles only.
    """
    templates = request.app.state.templates
    form = await request.form()

    # Parse form — date, pages, recency filter, and sort
    date_posted = form.get("date_posted") or "week"
    num_pages = min(int(form.get("num_pages") or 1), 3)
    max_days_ago_raw = form.get("max_days_ago", "").strip()
    max_days_ago = int(max_days_ago_raw) if max_days_ago_raw in ("7", "14", "30") else None
    sort_by = form.get("sort_by") or "location"
    if sort_by not in ("location", "score"):
        sort_by = "location"
    min_score = 55  # Hide SKIP-tier jobs (irrelevant results)

    # Seniority-qualified queries — search for Senior PM+ roles, not bare "Product Manager"
    search_queries = [
        "Senior Product Manager",
        "Lead Product Manager",
        "Group Product Manager",
        "Principal Product Manager",
        "Director of Product",
    ]

    try:
        from researcher.jsearch_client import search_jobs
        from researcher.job_scorer import load_pkb, _build_candidate_skills, _build_candidate_domains
        from researcher.lightweight_parser import lightweight_parse_jd, score_search_result

        # Load PKB once (needed for scoring)
        pkb = load_pkb()
        candidate_skills = _build_candidate_skills(pkb)
        candidate_domains = _build_candidate_domains(pkb)

        # Query India first (country + major cities), then US/global — JSearch defaults to US when no location
        all_jobs = []
        seen_urls = set()
        query_count = 0

        def _add_job(job):
            url = job.get("job_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_jobs.append(job)
            elif not url:
                key = f"{job.get('title', '')}@{job.get('company', '')}"
                if key not in seen_urls:
                    seen_urls.add(key)
                    all_jobs.append(job)

        # 1. India jobs: seniority-qualified queries across major cities
        india_locations = ("India", "Bangalore", "Hyderabad", "Mumbai", "Delhi", "Pune")
        for q in search_queries:
            for loc in india_locations:
                for job in search_jobs(
                    query=q,
                    location=loc,
                    num_pages=num_pages,
                    date_posted=date_posted,
                    remote_only=False,
                    country="in",
                ):
                    _add_job(job)

        # 2. Global/US jobs (no location = JSearch returns US-heavy results)
        for q in search_queries:
            for job in search_jobs(
                query=q,
                location="",
                num_pages=num_pages,
                date_posted=date_posted,
                remote_only=False,
            ):
                _add_job(job)

        query_count = len(search_queries) * (len(india_locations) + 1) * num_pages

        raw_count = len(all_jobs)

        # Keep only PM roles at Senior+ level — exclude junior/APM/VP+
        _EXCLUDE_TITLE_WORDS = [
            "associate product manager", "junior product manager", "junior pm",
            "associate pm", "apm", "product analyst", "product coordinator",
            "entry level", "assistant product manager",
            "vp product", "vp of product", "vice president product",
            "cpo", "chief product officer", "svp product", "evp product",
        ]

        def _is_senior_pm_role(title: str) -> bool:
            t = (title or "").lower()
            # Must be a PM role
            is_pm = (
                "product manager" in t
                or "product management" in t
                or "product lead" in t
                or "product owner" in t
                or "technical pm" in t
            )
            if not is_pm:
                return False
            # Exclude junior/APM/VP+ titles
            for excl in _EXCLUDE_TITLE_WORDS:
                if excl in t:
                    return False
            return True

        all_jobs = [j for j in all_jobs if _is_senior_pm_role(j.get("title"))]

        if not all_jobs:
            if raw_count == 0:
                hint = (
                    "JSearch API returned no results. Check that JSEARCH_API_KEY is set in .env "
                    "(get a free key at rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch). "
                    "Try a wider date range (e.g. This month)."
                )
            else:
                hint = f"API returned {raw_count} jobs but none had 'Product Manager' in the title. Try a wider date range."
            return HTMLResponse(
                f'<div class="text-center py-8 text-gray-500">'
                f'<p class="text-lg font-medium">No Product Manager roles found</p>'
                f'<p class="text-sm mt-1">{hint}</p></div>',
                status_code=200,
            )

        # Parse and score each job (PKB already loaded above)
        scored_jobs = []
        for job in all_jobs:
            # Lightweight parse
            parsed_jd = lightweight_parse_jd(
                description=job.get("description", ""),
                title=job.get("title", ""),
                company=job.get("company", ""),
                location=job.get("location", ""),
            )

            # Score with custom formula
            score = score_search_result(
                job=job,
                parsed_jd=parsed_jd,
                pkb=pkb,
                candidate_skills=candidate_skills,
                candidate_domains=candidate_domains,
            )

            # Min score filter
            if score["fit_score"] < min_score:
                continue

            # Store JD text for "Generate Resume" button
            job_id = job.get("jsearch_job_id") or job.get("title", "")
            web_state.search_job_descriptions[job_id] = job.get("description", "")

            scored_jobs.append({
                "title": job.get("title", "Unknown"),
                "company": job.get("company", "Unknown"),
                "location": job.get("location", ""),
                "job_url": job.get("job_url", ""),
                "source": job.get("source", ""),
                "posted_days_ago": job.get("posted_days_ago"),
                "employer_logo": job.get("employer_logo", ""),
                "job_publisher": job.get("job_publisher", ""),
                "job_id": job_id,
                "description": job.get("description", ""),
                "fit_score": score["fit_score"],
                "recommendation": score["recommendation"],
                "components": score["components"],
                "missing_critical_skills": score["missing_critical_skills"],
                "signals": parsed_jd.get("signals", {}),
            })

        # Optional recency filter: keep jobs with posted_days_ago <= max_days or unknown date
        filtered_by_recency = 0
        if max_days_ago:
            before = len(scored_jobs)
            scored_jobs = [
                j for j in scored_jobs
                if j.get("posted_days_ago") is None or j["posted_days_ago"] <= max_days_ago
            ]
            filtered_by_recency = before - len(scored_jobs)

        # Publisher priority: Career pages, LinkedIn, Google Jobs first (tier 0); others tier 1
        def _publisher_priority(pub: str) -> int:
            p = (pub or "").lower()
            if any(s in p for s in ["linkedin", "google", "google jobs", "career", "careers"]):
                return 0  # Preferred
            return 1  # Other (Indeed, Glassdoor, etc.)

        # Sort: by location (India first) or by score only; publisher priority first
        _INDIA = ["india", "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "delhi",
                  "gurgaon", "gurugram", "noida", "chennai", "kolkata"]
        _US = ["united states", "usa", " us ", "new york", "san francisco", "seattle",
               "austin", "boston", "chicago", "los angeles"]
        _REMOTE = ["remote", "work from home", "wfh", "anywhere", "distributed"]

        def _location_priority(loc: str) -> int:
            l = (loc or "").lower()
            if any(s in l for s in _INDIA):
                return 0  # India first
            if any(s in l for s in _US):
                return 1  # US second
            if any(s in l for s in _REMOTE):
                return 2  # Remote third
            return 3  # Others last

        if sort_by == "score":
            scored_jobs.sort(key=lambda j: (
                _publisher_priority(j.get("job_publisher", "") or j.get("source", "")),
                -j["fit_score"],
            ))
        else:
            scored_jobs.sort(key=lambda j: (
                _publisher_priority(j.get("job_publisher", "") or j.get("source", "")),
                _location_priority(j.get("location", "")),
                -j["fit_score"],
            ))

        # Compute stats (including India jobs diagnostic)
        india_count = sum(
            1 for j in scored_jobs
            if any(s in (j.get("location") or "").lower() for s in _INDIA)
        )
        # Publisher distribution (Google, LinkedIn, Indeed, etc.)
        publisher_counts = dict(Counter(
            (j.get("job_publisher") or j.get("source") or "Unknown")
            for j in scored_jobs
        ))

        stats = {
            "total": len(scored_jobs),
            "queries_run": query_count,
            "raw_results": len(all_jobs),
            "india_jobs": india_count,
            "apply_today": sum(1 for j in scored_jobs if j["recommendation"] == "APPLY TODAY"),
            "worth_trying": sum(1 for j in scored_jobs if j["recommendation"] == "WORTH TRYING"),
            "stretch": sum(1 for j in scored_jobs if j["recommendation"] == "STRETCH"),
            "skip": sum(1 for j in scored_jobs if j["recommendation"] == "SKIP"),
            "filtered_out": len(all_jobs) - len(scored_jobs),
            "filtered_by_recency": filtered_by_recency,
            "publisher_counts": publisher_counts,
        }

        # Optional: filter applied jobs
        hide_applied = form.get("hide_applied") == "1"
        applied_job_ids = load_applied_jobs()
        if hide_applied:
            scored_jobs = [j for j in scored_jobs if j.get("job_id") not in applied_job_ids]
            stats = dict(stats)
            stats["total"] = len(scored_jobs)

        # Persist for back-navigation restore (keep last 5)
        search_id = str(uuid.uuid4())
        store_data = {
            "jobs": scored_jobs,
            "stats": stats,
            "sort_by": sort_by,
            "hide_applied": hide_applied,
        }
        web_state.research_stores[search_id] = store_data
        while len(web_state.research_stores) > 5:
            oldest = next(iter(web_state.research_stores))
            del web_state.research_stores[oldest]

        # Persist to disk so results survive server restarts and page navigation
        save_last_search(search_id, scored_jobs, stats, sort_by, hide_applied)

        generated = load_generated_resumes()
        jobs_with_resumes = set(generated.keys())

        return templates.TemplateResponse(
            "partials/search_results.html",
            {
                "request": request,
                "jobs": scored_jobs,
                "stats": stats,
                "search_id": search_id,
                "jobs_with_resumes": jobs_with_resumes,
                "applied_job_ids": applied_job_ids,
                "hide_applied": hide_applied,
            },
        )

    except Exception as e:
        logger.exception("Search failed: %s", e)
        return HTMLResponse(
            f'<div class="border border-red-200 bg-red-50 rounded p-4 text-red-700 mt-4">'
            f'<p class="font-medium">Search failed</p>'
            f'<p class="text-sm mt-1">{str(e)}</p></div>',
            status_code=200,
        )
