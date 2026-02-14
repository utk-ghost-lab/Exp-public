"""Generate resume routes: form, run pipeline with SSE progress, result, finalize with optional edit."""

import asyncio
import json
import logging
import sys
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

# Fallback error HTML when templates fail (ensures we never return 500 to HTMX)
_ERROR_PARTIAL_HTML = '''<div class="border border-red-200 bg-red-50 rounded p-4 text-red-700">
<p class="font-medium">Something went wrong.</p>
<p class="text-sm mt-2">Please try again or paste a job description and click Generate Resume.</p>
</div>'''

# Add project root for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import config
from web import state as web_state

logger = logging.getLogger(__name__)

router = APIRouter()


def _run_pipeline_web(jd_text: str, job_id: str):
    """Run pipeline in thread; push progress to job_queues[job_id]; store result in job_stores[job_id]."""
    import queue
    q = queue.Queue()
    web_state.job_queues[job_id] = q

    def progress_callback(step: int, status: str, message: str, data: dict):
        q.put({"step": step, "status": status, "message": message, "data": data or {}})

    try:
        from main import run_pipeline
        result = run_pipeline(
            jd_text,
            review=False,
            fast=False,
            fast_no_improve=False,
            combined_parse_map=False,
            use_cache=True,
            progress_callback=progress_callback,
            stop_before_pdf=True,
        )
        if isinstance(result, dict):
            web_state.job_stores[job_id] = result
        q.put({"step": "complete", "status": "done", "message": "Complete", "data": {"job_id": job_id}})
    except Exception as e:
        logger.exception("Pipeline failed for job %s: %s", job_id, e)
        err_msg = str(e)
        if "529" in err_msg or "overloaded" in err_msg.lower():
            err_msg = "The AI service is temporarily overloaded. Please try again in a few minutes."
        elif len(err_msg) > 500:
            err_msg = err_msg[:500] + "..."
        web_state.job_stores[job_id] = {"error": err_msg}
        q.put({"step": "error", "status": "error", "message": err_msg, "data": {}})
    finally:
        if job_id in web_state.job_queues:
            del web_state.job_queues[job_id]


@router.get("", response_class=HTMLResponse)
async def get_generate_form(request: Request):
    """Show paste-JD form and progress/result placeholders."""
    templates = request.app.state.templates
    return templates.TemplateResponse("generate.html", {"request": request})


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.post("", response_class=HTMLResponse)
async def post_generate_start(request: Request):
    """Start pipeline; return HTML with job_id and SSE connection for progress.
    HTMX requests get a partial (progress or error) for swap; normal POST gets a full page.
    Always returns 200 so HTMX can swap; never 500.
    """
    is_htmx = _is_htmx(request)

    def _error_response(msg: str):
        """Return 200 with error content; use fallback HTML if templates fail."""
        try:
            templates = request.app.state.templates
            if is_htmx:
                return templates.TemplateResponse(
                    "partials/error_server.html",
                    {"request": request, "error": msg},
                )
            return templates.TemplateResponse(
                "generate.html",
                {"request": request, "error": msg},
            )
        except Exception:
            return HTMLResponse(content=_ERROR_PARTIAL_HTML, status_code=200)

    try:
        templates = request.app.state.templates
        form = await request.form()
        jd_text = (form.get("jd_text") or "").strip()

        if not jd_text:
            if is_htmx:
                return templates.TemplateResponse(
                    "partials/error_message.html",
                    {"request": request, "error": "Please paste a job description."},
                )
            return templates.TemplateResponse(
                "generate.html",
                {"request": request, "error": "Please paste a job description."},
            )

        job_id = str(uuid.uuid4())
        thread = threading.Thread(target=_run_pipeline_web, args=(jd_text, job_id))
        thread.daemon = True
        thread.start()

        if is_htmx:
            return templates.TemplateResponse(
                "partials/progress_wrapper.html",
                {"request": request, "job_id": job_id},
            )
        return templates.TemplateResponse(
            "generate_progress.html",
            {"request": request, "job_id": job_id},
        )
    except Exception as e:
        logger.exception("post_generate_start failed: %s", e)
        return _error_response("Something went wrong. Please try again.")


@router.get("/stream/{job_id}")
async def get_stream(job_id: str):
    """SSE stream of progress events for job_id."""
    import queue as queue_module
    q = web_state.job_queues.get(job_id)

    async def event_stream():
        if not q:
            if job_id in web_state.job_stores:
                state = web_state.job_stores[job_id]
                if "error" in state:
                    yield f"data: {json.dumps({'step': 'error', 'message': state['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'complete', 'job_id': job_id})}\n\n"
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


@router.get("/result/{job_id}", response_class=HTMLResponse)
async def get_result(request: Request, job_id: str):
    """Return result partial: score breakdown, download placeholder, edit form."""
    state = web_state.job_stores.get(job_id)
    templates = request.app.state.templates
    if not state:
        return HTMLResponse(content="<p>Job not found or not ready.</p>", status_code=404)
    if "error" in state:
        return templates.TemplateResponse(
            "partials/result_error.html",
            {"request": request, "error": state["error"]},
        )
    resume_content = state.get("resume_content", {})
    return templates.TemplateResponse(
        "partials/result_card.html",
        {
            "request": request,
            "job_id": job_id,
            "score_report": state.get("score_report", {}),
            "resume_content_json": json.dumps(resume_content, indent=2),
            "parsed_jd": state.get("parsed_jd", {}),
        },
    )


@router.post("/finalize", response_class=FileResponse)
async def post_finalize(request: Request):
    """Generate PDF (and artifacts); optionally use edited resume_content; record edits; return PDF."""
    body = await request.json()
    job_id = body.get("job_id")
    edited_content = body.get("resume_content")

    state = web_state.job_stores.get(job_id)
    if not state or "error" in state:
        return JSONResponse({"detail": "Job not found or failed."}, status_code=404)

    resume_content = edited_content if edited_content else state.get("resume_content", {})
    parsed_jd = state.get("parsed_jd", {})
    score_report = state.get("score_report", {})
    keyword_report = state.get("keyword_report", {})
    reframing_log = state.get("reframing_log", [])
    format_validation = state.get("format_validation", {})
    iteration_log = state.get("iteration_log", {})
    pkb = state.get("pkb", {})

    edit_record = None
    if edited_content:
        from engine.review_edit import append_human_edit_log, save_edit_record
        content_before = state.get("resume_content", {})
        from datetime import datetime, timezone
        edit_record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "jd_context": {
                "company": (parsed_jd.get("company") or "").strip() or "Not specified",
                "job_title": (parsed_jd.get("job_title") or "").strip(),
            },
            "content_before": content_before,
            "content_after": edited_content,
        }
        append_human_edit_log(edit_record)

    from engine.generator import generate_output, QualityGateBlockedError
    output_dir = str(config.PROJECT_ROOT / "output")
    try:
        out_folder = generate_output(
            formatted_content=resume_content,
            jd_analysis=parsed_jd,
            score_report=score_report,
            keyword_report=keyword_report,
            reframing_log=reframing_log,
            format_validation=format_validation,
            iteration_log=iteration_log,
            pkb=pkb,
            edit_record=edit_record,
            output_dir=output_dir,
            output_suffix=job_id[:8] if job_id else None,
        )
    except QualityGateBlockedError as e:
        logger.warning("Quality gate blocked PDF: %s", e.blocked_reason)
        return JSONResponse(
            {"detail": "Quality check failed. Please try again or contact support.", "blocked_reason": e.blocked_reason, "rule13_failures": e.rule13_failures},
            status_code=400,
        )
    if edit_record:
        save_edit_record(edit_record, out_folder)

    # Find PDF path
    pdf_path = None
    for f in Path(out_folder).iterdir():
        if f.suffix.lower() == ".pdf":
            pdf_path = str(f)
            break
    if not pdf_path:
        return JSONResponse({"detail": "PDF not generated."}, status_code=500)
    return FileResponse(pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)
