"""Research jobs routes: search form, URL paste, SSE progress, ranked results, daily shortlist."""

import asyncio
import json
import logging
import sys
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import state as web_state

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ===================================================================
# Background worker
# ===================================================================

def _run_research(search_id: str, mode: str, form_data: dict):
    """Run research pipeline in background thread.

    mode: 'search' (board search) or 'urls' (score specific URLs)
    """
    import queue
    q = queue.Queue()
    web_state.research_queues[search_id] = q

    def progress_cb(step, message):
        q.put({"step": step, "message": message})

    try:
        from researcher.daily_shortlist import run_shortlist

        if mode == "urls":
            urls = [u.strip() for u in form_data.get("urls", "").split("\n") if u.strip()]
            result = run_shortlist(skip_search=True, urls=urls, progress_cb=progress_cb)
        else:
            result = run_shortlist(skip_search=False, urls=None, progress_cb=progress_cb)

        web_state.research_stores[search_id] = {
            "jobs": result.get("jobs", []),
            "stats": result.get("stats", {}),
            "shortlist_path": result.get("shortlist_path", ""),
        }
        q.put({"step": "complete", "message": "Research complete",
               "data": {"search_id": search_id}})

    except Exception as e:
        logger.exception("Research failed for %s: %s", search_id, e)
        web_state.research_stores[search_id] = {"error": str(e)}
        q.put({"step": "error", "message": str(e)})
    finally:
        if search_id in web_state.research_queues:
            del web_state.research_queues[search_id]


# ===================================================================
# Routes
# ===================================================================

@router.get("", response_class=HTMLResponse)
async def get_research_page(request: Request):
    """Main research page — search form + URL paste tabs + daily shortlist."""
    templates = request.app.state.templates
    # Load latest daily shortlist if available
    research_dir = PROJECT_ROOT / "data" / "research"
    shortlist_md = ""
    if research_dir.exists():
        md_files = sorted(research_dir.glob("shortlist_*.md"), reverse=True)
        if md_files:
            shortlist_md = md_files[0].read_text()
    return templates.TemplateResponse("research.html", {
        "request": request,
        "shortlist_md": shortlist_md,
    })


@router.post("/search", response_class=HTMLResponse)
async def post_search(request: Request):
    """Start bulk board search — returns progress partial with SSE connection."""
    templates = request.app.state.templates
    search_id = str(uuid.uuid4())

    thread = threading.Thread(target=_run_research,
                              args=(search_id, "search", {}))
    thread.daemon = True
    thread.start()

    if _is_htmx(request):
        return templates.TemplateResponse(
            "partials/research_progress.html",
            {"request": request, "search_id": search_id},
        )
    return templates.TemplateResponse(
        "research.html",
        {"request": request, "search_id": search_id, "shortlist_md": ""},
    )


@router.post("/urls", response_class=HTMLResponse)
async def post_urls(request: Request):
    """Score pasted URLs — returns progress partial with SSE connection."""
    templates = request.app.state.templates
    form = await request.form()
    urls_text = (form.get("urls") or "").strip()

    if not urls_text:
        if _is_htmx(request):
            return HTMLResponse(
                '<p class="text-red-600 text-sm">Please paste at least one job URL.</p>',
                status_code=200,
            )
        return templates.TemplateResponse(
            "research.html",
            {"request": request, "error": "Please paste at least one URL.", "shortlist_md": ""},
        )

    search_id = str(uuid.uuid4())

    thread = threading.Thread(target=_run_research,
                              args=(search_id, "urls", {"urls": urls_text}))
    thread.daemon = True
    thread.start()

    if _is_htmx(request):
        return templates.TemplateResponse(
            "partials/research_progress.html",
            {"request": request, "search_id": search_id},
        )
    return templates.TemplateResponse(
        "research.html",
        {"request": request, "search_id": search_id, "shortlist_md": ""},
    )


@router.get("/stream/{search_id}")
async def get_stream(search_id: str):
    """SSE stream of research progress events."""
    import queue as queue_module
    q = web_state.research_queues.get(search_id)

    async def event_stream():
        if not q:
            # Already finished
            if search_id in web_state.research_stores:
                state = web_state.research_stores[search_id]
                if "error" in state:
                    yield f"data: {json.dumps({'step': 'error', 'message': state['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'complete', 'data': {'search_id': search_id}})}\n\n"
            return
        while True:
            try:
                item = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: q.get(timeout=0.5)
                )
            except queue_module.Empty:
                continue
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("step") in ("complete", "error"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/results/{search_id}", response_class=HTMLResponse)
async def get_results(request: Request, search_id: str):
    """Return ranked results table partial."""
    templates = request.app.state.templates
    state = web_state.research_stores.get(search_id)

    if not state:
        return HTMLResponse('<p class="text-gray-500">Results not found.</p>', status_code=404)
    if "error" in state:
        return HTMLResponse(
            f'<p class="text-red-600">Research failed: {state["error"]}</p>',
            status_code=200,
        )

    jobs = state.get("jobs", [])
    stats = state.get("stats", {})

    # Load watchlist for spike detection
    from researcher.company_analyzer import load_watchlist
    watchlist = load_watchlist()
    spike_companies = set()
    for name, info in watchlist.get("companies", {}).items():
        if info.get("hiring_spike", {}).get("spike"):
            spike_companies.add(name.lower())

    # Prepare jobs for template — strip heavy fields
    display_jobs = []
    for job in jobs:
        score = job.get("score", {})
        display_jobs.append({
            "title": job.get("title", "Unknown"),
            "company": job.get("company", "Unknown"),
            "location": job.get("location", ""),
            "job_url": job.get("job_url", ""),
            "source": job.get("source", ""),
            "posted_days_ago": job.get("posted_days_ago"),
            "fit_score": score.get("fit_score", 0),
            "recommendation": score.get("recommendation", "SKIP"),
            "components": score.get("components", {}),
            "missing_critical_skills": score.get("missing_critical_skills", []),
            "jd_text": job.get("description", ""),
            "salary_signal": job.get("salary_signal"),
            "why_this_fits": job.get("why_this_fits", ""),
        })

    return templates.TemplateResponse(
        "partials/research_results.html",
        {
            "request": request,
            "jobs": display_jobs,
            "stats": stats,
            "search_id": search_id,
            "spike_companies": spike_companies,
        },
    )


@router.get("/daily", response_class=HTMLResponse)
async def get_daily_shortlist(request: Request):
    """View today's daily shortlist markdown."""
    templates = request.app.state.templates
    research_dir = PROJECT_ROOT / "data" / "research"
    shortlist_md = ""
    if research_dir.exists():
        md_files = sorted(research_dir.glob("shortlist_*.md"), reverse=True)
        if md_files:
            shortlist_md = md_files[0].read_text()

    if not shortlist_md:
        shortlist_md = "No daily shortlist generated yet. Run a search first."

    return templates.TemplateResponse("research.html", {
        "request": request,
        "shortlist_md": shortlist_md,
        "tab": "daily",
    })
