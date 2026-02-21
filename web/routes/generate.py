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
from urllib.parse import unquote

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
from web.resume_store import save_generated_resume, load_generated_resumes, add_applied_job

logger = logging.getLogger(__name__)

router = APIRouter()


def _run_pipeline_web(jd_text: str, job_id: str, search_job_id: str = ""):
    """Run pipeline in thread; push progress to job_queues[job_id]; store result in job_stores[job_id]."""
    import queue
    q = queue.Queue()
    web_state.job_queues[job_id] = q

    def _store(state: dict):
        if search_job_id:
            state["search_job_id"] = search_job_id
        web_state.job_stores[job_id] = state

    def progress_callback(step, status: str, message: str, data: dict):
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
            enable_research=True,
        )
        if isinstance(result, dict):
            _store(result)
        q.put({"step": "complete", "status": "done", "message": "Complete", "data": {"job_id": job_id}})
    except FileNotFoundError as e:
        logger.exception("Pipeline failed for job %s: %s", job_id, e)
        err_msg = "Profile not found. Run 'python main.py --build-profile' first to create your Profile Knowledge Base."
        _store({"error": err_msg})
        q.put({"step": "error", "status": "error", "message": err_msg, "data": {}})
    except Exception as e:
        logger.exception("Pipeline failed for job %s: %s", job_id, e)
        err_msg = str(e)
        if "529" in err_msg or "overloaded" in err_msg.lower():
            err_msg = "The AI service is temporarily overloaded. Please try again in a few minutes."
        elif "rate limit" in err_msg.lower() or "429" in err_msg:
            err_msg = "API rate limit reached. Please try again in a few minutes."
        elif "timeout" in err_msg.lower():
            err_msg = "Request timed out. The job description may be too long. Please try again."
        elif len(err_msg) > 800:
            err_msg = err_msg[:800] + "..."
        _store({"error": err_msg})
        q.put({"step": "error", "status": "error", "message": err_msg, "data": {}})
    finally:
        if job_id in web_state.job_queues:
            del web_state.job_queues[job_id]


@router.get("", response_class=HTMLResponse)
async def get_generate_form(request: Request):
    """Show paste-JD form and progress/result placeholders.
    Accepts ?job_id= to pre-fill JD text from a search result.
    """
    templates = request.app.state.templates
    prefill_jd = ""
    job_id = request.query_params.get("job_id", "")
    if job_id:
        prefill_jd = web_state.search_job_descriptions.get(job_id, "")
    return templates.TemplateResponse("generate.html", {
        "request": request,
        "prefill_jd": prefill_jd,
    })


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
        search_job_id = (form.get("job_id") or "").strip()

        # When coming from research page: job_id fetches JD from search_job_descriptions
        if not jd_text and search_job_id:
            jd_text = web_state.search_job_descriptions.get(search_job_id, "").strip()

        if not jd_text:
            err_msg = "Job description not found. Try searching again or paste the JD manually." if search_job_id else "Please paste a job description."
            if is_htmx:
                return templates.TemplateResponse(
                    "partials/error_message.html",
                    {"request": request, "error": err_msg},
                )
            return templates.TemplateResponse(
                "generate.html",
                {"request": request, "error": err_msg},
            )

        job_id = str(uuid.uuid4())
        thread = threading.Thread(target=_run_pipeline_web, args=(jd_text, job_id, search_job_id or ""))
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
            "research_brief": state.get("research_brief"),
        },
    )


@router.get("/download/{search_job_id:path}", response_class=FileResponse)
async def get_download(search_job_id: str):
    """Download generated resume PDF by search job ID. Marks job as applied."""
    search_job_id = unquote(search_job_id)
    mapping = load_generated_resumes()
    out_folder = mapping.get(search_job_id)
    if not out_folder or not Path(out_folder).is_dir():
        return JSONResponse({"detail": "Resume not found for this job."}, status_code=404)
    pdf_path = None
    for f in Path(out_folder).iterdir():
        if f.suffix.lower() == ".pdf":
            pdf_path = str(f)
            break
    if not pdf_path:
        return JSONResponse({"detail": "PDF not found."}, status_code=404)
    add_applied_job(search_job_id)
    pdf_filename = Path(pdf_path).name
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_filename)


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
    research_brief = state.get("research_brief")

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
            research_brief=research_brief,
        )
    except QualityGateBlockedError as e:
        failures = e.rule13_failures or []
        msg_map = {
            "title_fabrication": "Resume title doesn't match your profile.",
            "pre_2023_anachronistic_tech": "Pre-2023 role mentions LLM/GPT (not available then).",
            "no_pre_2023_llm_powered": "Pre-2023 work claims LLM-powered.",
            "no_banned_verb_starts": "A bullet starts with a weak verb (Managed, Helped, etc.).",
            "every_bullet_has_metric": "Some bullets lack numbers or metrics.",
        }
        details = [msg_map.get(f, f) for f in failures]
        detail_msg = "Quality check failed: " + "; ".join(details) if details else "Quality check failed. Please try again or contact support."
        logger.warning("Quality gate blocked PDF: %s — %s", e.blocked_reason, failures)
        return JSONResponse(
            {"detail": detail_msg, "blocked_reason": e.blocked_reason, "rule13_failures": failures},
            status_code=400,
        )
    if edit_record:
        save_edit_record(edit_record, out_folder)

    # Persist mapping for download-on-card (legacy search flow)
    search_job_id = state.get("search_job_id", "")
    if search_job_id:
        save_generated_resume(search_job_id, out_folder)
        add_applied_job(search_job_id)

    # Register in apply queue for unified Command Center access (idempotent)
    from apply_manager import register_external_job as _register_external_job
    apply_job_id = web_state.job_stores[job_id].get("apply_job_id")
    if not apply_job_id:
        apply_job_id = _register_external_job(
            title=parsed_jd.get("job_title", ""),
            company=parsed_jd.get("company", ""),
            output_folder=out_folder,
            resume_score=score_report.get("total_score", 0),
            job_url=state.get("job_url", ""),
        )
        web_state.job_stores[job_id]["apply_job_id"] = apply_job_id
    web_state.job_stores[job_id]["output_folder"] = str(out_folder)

    return JSONResponse({
        "status": "ok",
        "apply_job_id": apply_job_id,
        "out_folder": str(out_folder),
    })
