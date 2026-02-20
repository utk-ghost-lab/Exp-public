"""Apply Manager routes: dashboard, search, selection, generation, download, actions."""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import apply_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Separate SSE progress queues for search vs generation
_search_progress_queue: queue.Queue | None = None
_generate_progress_queue: queue.Queue | None = None
_progress_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def get_apply_page(request: Request):
    """Render the Apply Manager dashboard."""
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    if tab not in ("fresh", "all"):
        tab = "fresh"
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("apply.html", {
        "request": request,
        **data,
    })


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.post("/search-now", response_class=HTMLResponse)
async def post_search_now(request: Request):
    """Trigger a search-only run in a daemon thread."""
    global _search_progress_queue
    templates = request.app.state.templates

    if apply_manager.is_run_active():
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "search",
            "message": "An operation is already in progress...",
        })

    with _progress_lock:
        _search_progress_queue = queue.Queue()

    def _on_progress(msg: str):
        with _progress_lock:
            if _search_progress_queue is not None:
                _search_progress_queue.put(msg)

    started = apply_manager.start_search_thread(progress_cb=_on_progress)
    if not started:
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "search",
            "message": "An operation is already in progress...",
        })

    return templates.TemplateResponse("partials/apply_run_status.html", {
        "request": request,
        "running": True,
        "operation": "search",
        "message": "Searching for jobs...",
    })


@router.get("/search-progress")
async def get_search_progress():
    """SSE stream for search progress messages."""
    import asyncio

    async def event_stream():
        while True:
            msg = None
            with _progress_lock:
                if _search_progress_queue is not None:
                    try:
                        msg = _search_progress_queue.get_nowait()
                    except queue.Empty:
                        pass

            if msg is not None:
                data = json.dumps({"message": msg, "running": apply_manager.is_search_active()})
                yield f"data: {data}\n\n"
                if not apply_manager.is_search_active():
                    yield f"data: {json.dumps({'message': msg, 'running': False, 'done': True})}\n\n"
                    return
            else:
                if not apply_manager.is_search_active():
                    yield f"data: {json.dumps({'message': 'Search complete.', 'running': False, 'done': True})}\n\n"
                    return
                await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Selection & Generation
# ---------------------------------------------------------------------------

@router.post("/generate-selected", response_class=HTMLResponse)
async def post_generate_selected(request: Request):
    """Select jobs and start generation. Expects form with job_ids[] checkboxes."""
    global _generate_progress_queue
    templates = request.app.state.templates
    form = await request.form()

    job_ids = form.getlist("job_ids")
    if not job_ids:
        data = apply_manager.get_dashboard_data(tab="fresh")
        return templates.TemplateResponse("partials/apply_job_list.html", {
            "request": request,
            **data,
        })

    # Mark as selected
    apply_manager.select_jobs_for_generation(job_ids)

    if apply_manager.is_run_active():
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "generate",
            "message": "An operation is already in progress...",
        })

    with _progress_lock:
        _generate_progress_queue = queue.Queue()

    def _on_progress(msg: str):
        with _progress_lock:
            if _generate_progress_queue is not None:
                _generate_progress_queue.put(msg)

    started = apply_manager.start_generate_thread(progress_cb=_on_progress)
    if not started:
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "generate",
            "message": "An operation is already in progress...",
        })

    return templates.TemplateResponse("partials/apply_run_status.html", {
        "request": request,
        "running": True,
        "operation": "generate",
        "message": f"Generating resumes for {len(job_ids)} selected jobs...",
    })


@router.post("/generate-single/{job_id}", response_class=HTMLResponse)
async def post_generate_single(request: Request, job_id: str):
    """Generate resume for a single job."""
    global _generate_progress_queue
    templates = request.app.state.templates

    if apply_manager.is_run_active():
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "generate",
            "message": "An operation is already in progress...",
        })

    with _progress_lock:
        _generate_progress_queue = queue.Queue()

    def _on_progress(msg: str):
        with _progress_lock:
            if _generate_progress_queue is not None:
                _generate_progress_queue.put(msg)

    started = apply_manager.start_single_generate_thread(job_id, progress_cb=_on_progress)
    if not started:
        return templates.TemplateResponse("partials/apply_run_status.html", {
            "request": request,
            "running": True,
            "operation": "generate",
            "message": "An operation is already in progress...",
        })

    return templates.TemplateResponse("partials/apply_run_status.html", {
        "request": request,
        "running": True,
        "operation": "generate",
        "message": "Generating resume...",
    })


@router.get("/generate-progress")
async def get_generate_progress():
    """SSE stream for generation progress messages."""
    import asyncio

    async def event_stream():
        while True:
            msg = None
            with _progress_lock:
                if _generate_progress_queue is not None:
                    try:
                        msg = _generate_progress_queue.get_nowait()
                    except queue.Empty:
                        pass

            if msg is not None:
                data = json.dumps({"message": msg, "running": apply_manager.is_generate_active()})
                yield f"data: {data}\n\n"
                if not apply_manager.is_generate_active():
                    yield f"data: {json.dumps({'message': msg, 'running': False, 'done': True})}\n\n"
                    return
            else:
                if not apply_manager.is_generate_active():
                    yield f"data: {json.dumps({'message': 'Generation complete.', 'running': False, 'done': True})}\n\n"
                    return
                await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

@router.get("/download/{job_id}")
async def get_download(request: Request, job_id: str):
    """Serve generated PDF or DOCX for a job. Use ?format=docx for Word."""
    fmt = request.query_params.get("format", "pdf").lower()
    data = apply_manager.get_dashboard_data(tab="all")
    all_jobs = data["ready"] + data["applied"] + data["in_progress"] + data["failed"]
    job = next((j for j in all_jobs if j.get("job_id") == job_id), None)
    if not job or not job.get("output_folder"):
        return JSONResponse({"detail": "Resume not found for this job."}, status_code=404)

    out_dir = Path(job["output_folder"])
    if not out_dir.is_dir():
        return JSONResponse({"detail": "Output folder not found."}, status_code=404)

    ext = ".docx" if fmt == "docx" else ".pdf"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if fmt == "docx" else "application/pdf"

    target_path = None
    for f in out_dir.iterdir():
        if f.suffix.lower() == ext:
            target_path = f
            break
    if not target_path:
        return JSONResponse({"detail": f"{ext.upper()} not found in output folder."}, status_code=404)

    return FileResponse(str(target_path), media_type=media_type, filename=target_path.name)


# ---------------------------------------------------------------------------
# Cover letter & LinkedIn message
# ---------------------------------------------------------------------------

@router.post("/cover-letter/{job_id}", response_class=JSONResponse)
async def post_cover_letter(job_id: str):
    """Generate a cover letter for a ready job."""
    result = apply_manager.generate_cover_letter_for_job(job_id)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.get("/download-cover-letter/{job_id}")
async def get_download_cover_letter(job_id: str):
    """Download cover letter text file."""
    data = apply_manager.get_dashboard_data(tab="all")
    all_jobs = data["ready"] + data["applied"]
    job = next((j for j in all_jobs if j.get("job_id") == job_id), None)
    if not job or not job.get("output_folder"):
        return JSONResponse({"detail": "Job not found."}, status_code=404)

    cl_path = Path(job["output_folder"]) / "cover_letter.txt"
    if not cl_path.exists():
        return JSONResponse({"detail": "Cover letter not generated yet."}, status_code=404)

    return FileResponse(str(cl_path), media_type="text/plain", filename=f"cover_letter_{job.get('company', 'company')}.txt")


@router.post("/linkedin-message/{job_id}", response_class=JSONResponse)
async def post_linkedin_message(request: Request, job_id: str):
    """Generate a LinkedIn message for a ready job."""
    try:
        body = await request.json()
        message_type = body.get("message_type", "connection_request")
    except Exception:
        message_type = "connection_request"
    result = apply_manager.generate_linkedin_message_for_job(job_id, message_type)
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.get("/linkedin-message/{job_id}", response_class=JSONResponse)
async def get_linkedin_message(job_id: str):
    """Get saved LinkedIn message for a job."""
    data = apply_manager.get_dashboard_data(tab="all")
    all_jobs = data["ready"] + data["applied"]
    job = next((j for j in all_jobs if j.get("job_id") == job_id), None)
    if not job or not job.get("output_folder"):
        return JSONResponse({"detail": "Job not found."}, status_code=404)

    msg_path = Path(job["output_folder"]) / "linkedin_message.txt"
    if not msg_path.exists():
        return JSONResponse({"detail": "LinkedIn message not generated yet."}, status_code=404)

    return JSONResponse({"status": "ok", "text": msg_path.read_text()})


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@router.post("/mark-applied/{job_id}", response_class=HTMLResponse)
async def post_mark_applied(request: Request, job_id: str):
    """Mark job as applied. Returns updated job list partial."""
    apply_manager.mark_applied(job_id)
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("partials/apply_job_list.html", {
        "request": request,
        **data,
    })


@router.post("/retry/{job_id}", response_class=HTMLResponse)
async def post_retry(request: Request, job_id: str):
    """Reset failed job to selected. Returns updated job list partial."""
    apply_manager.retry_failed(job_id)
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("partials/apply_job_list.html", {
        "request": request,
        **data,
    })


@router.post("/skip/{job_id}", response_class=HTMLResponse)
async def post_skip(request: Request, job_id: str):
    """Skip a discovered job. Returns updated job list partial."""
    apply_manager.skip_job(job_id)
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("partials/apply_job_list.html", {
        "request": request,
        **data,
    })


@router.post("/cancel/{job_id}", response_class=HTMLResponse)
async def post_cancel(request: Request, job_id: str):
    """Cancel a queued job back to discovered. Returns updated job list partial."""
    apply_manager.cancel_generation(job_id)
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("partials/apply_job_list.html", {
        "request": request,
        **data,
    })


# ---------------------------------------------------------------------------
# Partials
# ---------------------------------------------------------------------------

@router.get("/jobs-partial", response_class=HTMLResponse)
async def get_jobs_partial(request: Request):
    """HTMX partial: refreshed job card list."""
    templates = request.app.state.templates
    tab = request.query_params.get("tab", "fresh")
    data = apply_manager.get_dashboard_data(tab=tab)
    return templates.TemplateResponse("partials/apply_job_list.html", {
        "request": request,
        **data,
    })
