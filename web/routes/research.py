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

    try:
        from researcher.search_and_score import search_and_score

        scored_jobs = search_and_score(
            date_posted=date_posted,
            num_pages=num_pages,
            min_score=min_score,
        )

        if not scored_jobs:
            return HTMLResponse(
                '<div class="text-center py-8 text-gray-500">'
                '<p class="text-lg font-medium">No Product Manager roles found</p>'
                '<p class="text-sm mt-1">Try a wider date range (e.g. This month).</p></div>',
                status_code=200,
            )

        # Store JD text for "Generate Resume" button
        for j in scored_jobs:
            job_id = j.get("job_id", "")
            if job_id and j.get("description"):
                web_state.search_job_descriptions[job_id] = j["description"]

        # Compute query_count for stats (approximate)
        query_count = 2 * (3 + 1) * num_pages  # 2 queries * (3 india + 1 global) * pages

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
