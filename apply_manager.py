"""Apply Manager — Manual search → select → generate workflow.

Decoupled pipeline:
  1. run_search_only()      — search + dedup, save as status="discovered"
  2. select_jobs_for_generation() — mark chosen jobs as "selected"
  3. run_generation_for_selected() — generate resumes for selected jobs only
  4. generate_single_resume() — generate for one job (also used for retry)

Status flow:
  discovered → selected → queued → generating → ready → applied
                                               → failed (→ retry → selected)
           → skipped

Data is persisted to data/apply_queue.json.
"""

from __future__ import annotations

import json
import logging
import threading
import datetime
import time
import uuid
from pathlib import Path

from researcher.search_and_score import jd_hash, search_and_score

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
QUEUE_FILE = DATA_DIR / "apply_queue.json"

MIN_JD_LINES = 10


def _jd_line_count(description: str) -> int:
    """Count non-empty lines in a JD description."""
    if not description:
        return 0
    return sum(1 for line in description.splitlines() if line.strip())

_lock = threading.Lock()
_active_search_thread: threading.Thread | None = None
_active_generate_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# Queue persistence
# ---------------------------------------------------------------------------

def _load_queue() -> dict:
    """Load apply queue from disk. Returns default structure if missing."""
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt apply_queue.json — starting fresh")
    return {"runs": [], "jobs": {}}


def _save_queue(data: dict):
    """Write apply queue to disk atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(QUEUE_FILE)


# ---------------------------------------------------------------------------
# Resume generation wrapper
# ---------------------------------------------------------------------------

def _generate_resume_for_job(job_id: str, jd_text: str, tier: str) -> dict:
    """Run main.run_pipeline() for a single job. Returns dict with output_folder and resume_score,
    or raises on failure."""
    from main import run_pipeline

    # Fix tiering: full tier uses fast=False; fast tier uses fast=True
    if tier == "full":
        result = run_pipeline(
            jd_text,
            review=False,
            fast=False,
            fast_no_improve=False,
            combined_parse_map=False,
            use_cache=True,
            enable_research=True,
        )
    else:
        result = run_pipeline(
            jd_text,
            review=False,
            fast=True,
            fast_no_improve=True,
            combined_parse_map=False,
            use_cache=True,
            enable_research=False,
        )

    # run_pipeline returns output folder path (str) or dict if blocked
    if isinstance(result, dict) and result.get("blocked"):
        raise RuntimeError(f"Quality gate blocked: {result.get('blocked_reason', 'unknown')}")

    output_folder = result
    # Extract resume score from score_report.json in output
    score = 0.0
    score_path = Path(output_folder) / "score_report.json"
    if score_path.exists():
        try:
            with open(score_path) as f:
                sr = json.load(f)
            score = sr.get("total_score", 0)
        except Exception:
            pass

    return {"output_folder": str(output_folder), "resume_score": score}


# ---------------------------------------------------------------------------
# Search only (Step 1 of manual workflow)
# ---------------------------------------------------------------------------

def run_search_only(
    progress_cb=None,
    date_posted: str = "week",
    num_pages: int = 1,
    min_score: int = 65,
    sort_by: str = "score",
) -> dict:
    """Search for jobs and save as discovered. Does NOT generate resumes.

    Returns run summary dict.
    """
    def _notify(msg):
        logger.info("[ApplyManager] %s", msg)
        if progress_cb:
            progress_cb(msg)

    run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run_record = {
        "run_id": run_id,
        "started_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "status": "running",
        "type": "search",
        "jobs_found": 0,
        "jobs_new": 0,
        "filters": {
            "date_posted": date_posted,
            "scope": "India (all) + Global remote",
            "roles": "Senior PM, Lead PM, Principal PM, GPM, Director of Product",
            "min_score": min_score,
            "num_pages": num_pages,
            "sort_by": sort_by,
        },
    }

    with _lock:
        q = _load_queue()
        q["runs"].append(run_record)
        _save_queue(q)

    _notify("Searching for jobs...")

    try:
        scored_jobs, _ = search_and_score(
            date_posted=date_posted, num_pages=num_pages, min_score=min_score,
            sort_by=sort_by, progress_cb=_notify,
        )
    except Exception as e:
        logger.exception("Search failed: %s", e)
        _notify(f"Search failed: {e}")
        with _lock:
            q = _load_queue()
            for r in q["runs"]:
                if r["run_id"] == run_id:
                    r["status"] = "failed"
                    r["completed_at"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S")
            _save_queue(q)
        return {"run_id": run_id, "status": "failed", "error": str(e)}

    _notify(f"Found {len(scored_jobs)} matching jobs. Filtering duplicates...")

    # Dedup against existing queue (by description_hash)
    new_jobs = []
    with _lock:
        q = _load_queue()
        existing_hashes = {
            j.get("description_hash") for j in q["jobs"].values()
            if j.get("status") not in ("skipped",)
        }
        for sj in scored_jobs:
            h = sj.get("description_hash", jd_hash(sj.get("description", "")))
            if h not in existing_hashes:
                new_jobs.append(sj)
                existing_hashes.add(h)

    _notify(f"{len(new_jobs)} new jobs after dedup.")

    # Add new jobs as "discovered" (thin-JD jobs stored as skipped_thin_jd)
    skipped_thin = 0
    discovered_count = 0
    with _lock:
        q = _load_queue()
        for sj in new_jobs:
            apply_id = f"apply_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
            desc = sj.get("description", "")
            line_count = _jd_line_count(desc)
            if line_count < MIN_JD_LINES:
                logger.info(
                    "Skipping '%s' @ %s — JD too thin (%d lines)",
                    sj.get("title"), sj.get("company"), line_count,
                )
                q["jobs"][apply_id] = {
                    "job_id": apply_id,
                    "run_id": run_id,
                    "title": sj.get("title", "Unknown"),
                    "company": sj.get("company", "Unknown"),
                    "location": sj.get("location", ""),
                    "job_url": sj.get("job_url", ""),
                    "fit_score": sj.get("fit_score", 0),
                    "status": "skipped_thin_jd",
                    "skip_reason": f"JD too thin ({line_count} lines, need {MIN_JD_LINES}+)",
                    "description_hash": sj.get("description_hash", jd_hash(sj.get("description", ""))),
                    "description": desc,
                    "job_publisher": sj.get("job_publisher", ""),
                    "posted_days_ago": sj.get("posted_days_ago"),
                }
                skipped_thin += 1
                continue

            tier = "full" if sj["fit_score"] >= 80 else "fast"
            q["jobs"][apply_id] = {
                "job_id": apply_id,
                "run_id": run_id,
                "title": sj.get("title", "Unknown"),
                "company": sj.get("company", "Unknown"),
                "location": sj.get("location", ""),
                "job_url": sj.get("job_url", ""),
                "fit_score": sj.get("fit_score", 0),
                "recommendation": sj.get("recommendation", ""),
                "tier": tier,
                "status": "discovered",
                "output_folder": None,
                "resume_score": None,
                "error": None,
                "description_hash": sj.get("description_hash", jd_hash(sj.get("description", ""))),
                "description": desc,
                "components": sj.get("components", {}),
                "missing_critical_skills": sj.get("missing_critical_skills", []),
                "signals": sj.get("signals", {}),
                "job_publisher": sj.get("job_publisher", ""),
                "posted_days_ago": sj.get("posted_days_ago"),
            }
            discovered_count += 1

        for r in q["runs"]:
            if r["run_id"] == run_id:
                r["jobs_found"] = len(scored_jobs)
                r["jobs_new"] = discovered_count
                r["status"] = "completed"
                r["completed_at"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S")
        _save_queue(q)

    if skipped_thin:
        _notify(f"Skipped {skipped_thin} job(s): JD too short (< {MIN_JD_LINES} lines)")

    summary_msg = f"Search complete: {discovered_count} new jobs discovered out of {len(scored_jobs)} found."
    _notify(summary_msg)

    return {
        "run_id": run_id,
        "status": "completed",
        "jobs_found": len(scored_jobs),
        "jobs_new": discovered_count,
        "jobs_skipped_thin": skipped_thin,
    }


# ---------------------------------------------------------------------------
# Selection (Step 2)
# ---------------------------------------------------------------------------

def select_jobs_for_generation(job_ids: list[str]) -> int:
    """Mark discovered jobs as selected for generation. Returns count of jobs selected."""
    selected = 0
    with _lock:
        q = _load_queue()
        for jid in job_ids:
            job = q["jobs"].get(jid)
            if job and job.get("status") in ("discovered", "skipped"):
                job["status"] = "selected"
                selected += 1
        if selected:
            _save_queue(q)
    return selected


# ---------------------------------------------------------------------------
# Generation for selected jobs (Step 3)
# ---------------------------------------------------------------------------

def run_generation_for_selected(progress_cb=None) -> dict:
    """Generate resumes for all jobs with status='selected'.

    Returns summary dict.
    """
    def _notify(msg):
        logger.info("[ApplyManager] %s", msg)
        if progress_cb:
            progress_cb(msg)

    # Collect selected jobs and set to queued
    with _lock:
        q = _load_queue()
        queued_ids = []
        for jid, job in q["jobs"].items():
            if job.get("status") == "selected":
                job["status"] = "queued"
                queued_ids.append(jid)
        if queued_ids:
            _save_queue(q)

    if not queued_ids:
        _notify("No jobs selected for generation.")
        return {"status": "completed", "generated": 0, "failed": 0}

    _notify(f"Generating resumes for {len(queued_ids)} selected jobs...")

    generated = 0
    failed = 0

    for i, apply_id in enumerate(queued_ids, 1):
        with _lock:
            q = _load_queue()
            job = q["jobs"].get(apply_id)
            if not job:
                continue
            jd_text = job.get("description", "")
            tier = job.get("tier", "fast")
            job["status"] = "generating"
            _save_queue(q)

        _notify(f"Generating {i}/{len(queued_ids)}: {job.get('title', '')} @ {job.get('company', '')} [{tier}]")

        try:
            result = _generate_resume_for_job(apply_id, jd_text, tier)
            with _lock:
                q = _load_queue()
                j = q["jobs"].get(apply_id, {})
                j["status"] = "ready"
                j["output_folder"] = result["output_folder"]
                j["resume_score"] = result["resume_score"]
                j["error"] = None
                # NEVER delete JD text — needed for retry
                _save_queue(q)
            generated += 1
        except Exception as e:
            logger.exception("Generation failed for %s: %s", apply_id, e)
            err_msg = str(e)
            if len(err_msg) > 500:
                err_msg = err_msg[:500] + "..."
            with _lock:
                q = _load_queue()
                j = q["jobs"].get(apply_id, {})
                j["status"] = "failed"
                j["error"] = err_msg
                # NEVER delete JD text — needed for retry
                _save_queue(q)
            failed += 1

    summary_msg = f"Done: {generated} resumes generated, {failed} failed."
    _notify(summary_msg)

    return {"status": "completed", "generated": generated, "failed": failed}


def generate_single_resume(job_id: str, progress_cb=None) -> dict:
    """Generate a resume for a single job. Used for individual generation and retry."""
    def _notify(msg):
        logger.info("[ApplyManager] %s", msg)
        if progress_cb:
            progress_cb(msg)

    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job:
            return {"status": "error", "error": "Job not found"}
        if job.get("status") not in ("discovered", "selected", "failed", "queued"):
            return {"status": "error", "error": f"Job status is '{job.get('status')}', cannot generate"}
        jd_text = job.get("description", "")
        if not jd_text:
            return {"status": "error", "error": "No JD text available (description deleted)"}
        tier = job.get("tier", "fast")
        job["status"] = "generating"
        job["error"] = None
        _save_queue(q)

    _notify(f"Generating: {job.get('title', '')} @ {job.get('company', '')} [{tier}]")

    try:
        result = _generate_resume_for_job(job_id, jd_text, tier)
        with _lock:
            q = _load_queue()
            j = q["jobs"].get(job_id, {})
            j["status"] = "ready"
            j["output_folder"] = result["output_folder"]
            j["resume_score"] = result["resume_score"]
            j["error"] = None
            _save_queue(q)
        _notify(f"Resume ready: {job.get('title', '')} @ {job.get('company', '')}")
        return {"status": "ready", "output_folder": result["output_folder"], "resume_score": result["resume_score"]}
    except Exception as e:
        logger.exception("Generation failed for %s: %s", job_id, e)
        err_msg = str(e)
        if len(err_msg) > 500:
            err_msg = err_msg[:500] + "..."
        with _lock:
            q = _load_queue()
            j = q["jobs"].get(job_id, {})
            j["status"] = "failed"
            j["error"] = err_msg
            _save_queue(q)
        return {"status": "failed", "error": err_msg}


# ---------------------------------------------------------------------------
# Dashboard data
# ---------------------------------------------------------------------------

def get_dashboard_data(tab: str = "discover") -> dict:
    """Return jobs grouped by status for the 3-tab UI.

    tab="discover": discovered jobs from latest search run (default)
    tab="ready": all jobs with status 'ready'
    tab="applied": all jobs with status 'applied'

    Always returns in_progress counts (selected + queued + generating + failed)
    for the status bar shown above tabs.
    """
    with _lock:
        q = _load_queue()

    runs = q.get("runs", [])
    # Find latest search run
    latest_search_run_id = None
    latest_search_run = None
    for r in reversed(runs):
        if r.get("type") == "search" and r.get("status") == "completed":
            latest_search_run_id = r["run_id"]
            latest_search_run = r
            break

    # Collect ALL jobs into groups regardless of tab (needed for counts)
    all_discovered = []
    all_selected = []
    all_in_progress = []
    all_ready = []
    all_failed = []
    all_applied = []
    all_skipped = []

    # Discover tab: ALL discovered jobs, tagged with is_new
    discover_jobs = []

    for jid, job in q["jobs"].items():
        entry = dict(job)
        entry["job_id"] = jid
        status = job.get("status", "discovered")

        if status == "discovered":
            all_discovered.append(entry)
            entry["is_new"] = (job.get("run_id") == latest_search_run_id)
            discover_jobs.append(entry)
        elif status == "selected":
            all_selected.append(entry)
        elif status in ("queued", "generating"):
            all_in_progress.append(entry)
        elif status == "ready":
            all_ready.append(entry)
        elif status == "failed":
            all_failed.append(entry)
        elif status == "applied":
            all_applied.append(entry)
        elif status == "skipped_thin_jd":
            all_skipped.append(entry)

    # Sort each group by fit_score descending
    for group in (discover_jobs, all_discovered, all_selected, all_in_progress, all_ready, all_failed, all_applied, all_skipped):
        group.sort(key=lambda j: -j.get("fit_score", 0))

    # Select which jobs to show based on active tab
    if tab == "ready":
        tab_jobs = all_ready
    elif tab == "applied":
        tab_jobs = all_applied
    elif tab == "skipped":
        tab_jobs = all_skipped
    else:
        tab_jobs = discover_jobs

    last_run = runs[-1] if runs else None

    return {
        "tab": tab,
        "tab_jobs": tab_jobs,
        # Keep legacy group names for backwards compat with job list partial
        "discovered": discover_jobs if tab == "discover" else [],
        "selected": all_selected if tab == "discover" else [],
        "in_progress": all_in_progress if tab == "discover" else [],
        "ready": all_ready if tab == "ready" else [],
        "failed": all_failed if tab == "discover" else [],
        "applied": all_applied if tab == "applied" else [],
        "skipped": all_skipped if tab == "skipped" else [],
        "last_run": last_run,
        "last_search_run": latest_search_run,
        "counts": {
            "discovered": len(discover_jobs),
            "new": sum(1 for j in discover_jobs if j.get("is_new")),
            "selected": len(all_selected),
            "in_progress": len(all_in_progress),
            "ready": len(all_ready),
            "failed": len(all_failed),
            "applied": len(all_applied),
            "total_discovered": len(all_discovered),
            "skipped": len(all_skipped),
        },
    }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def get_job_by_id(job_id: str) -> dict | None:
    """Look up a single job by ID. Returns job dict with job_id key, or None."""
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job:
            return None
        entry = dict(job)
        entry["job_id"] = job_id
        return entry


def register_external_job(
    title: str,
    company: str,
    output_folder: str,
    resume_score: float = 0,
    job_url: str = "",
    source: str = "resume_generator",
) -> str:
    """Register a resume from the generate flow into the apply queue with status='ready'.
    Idempotent: if output_folder already registered under the same source, returns existing job_id."""
    with _lock:
        q = _load_queue()
        for jid, job in q["jobs"].items():
            if job.get("output_folder") == str(output_folder) and job.get("source") == source:
                return jid
        job_id = str(uuid.uuid4())[:8]
        q["jobs"][job_id] = {
            "title": title,
            "company": company,
            "job_url": job_url,
            "status": "ready",
            "source": source,
            "output_folder": str(output_folder),
            "resume_score": resume_score,
            "fit_score": int(resume_score),
            "tier": "full",
            "has_cover_letter": False,
            "has_linkedin_message": False,
        }
        _save_queue(q)
    return job_id


def mark_applied(job_id: str) -> bool:
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job:
            return False
        job["status"] = "applied"
        _save_queue(q)
    return True


def skip_job(job_id: str) -> bool:
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job or job.get("status") not in ("discovered", "selected"):
            return False
        job["status"] = "skipped"
        _save_queue(q)
    return True


def cancel_generation(job_id: str) -> bool:
    """Cancel a queued (not yet generating) job back to discovered."""
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job or job.get("status") != "queued":
            return False
        job["status"] = "discovered"
        _save_queue(q)
    return True


def retry_failed(job_id: str) -> bool:
    """Reset failed job to selected so it can be generated again."""
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job or job.get("status") != "failed":
            return False
        if not job.get("description"):
            logger.warning("Cannot retry %s: no JD text preserved", job_id)
            return False
        job["status"] = "selected"
        job["error"] = None
        _save_queue(q)
    return True


def recover_interrupted():
    """On startup: reset 'generating' jobs back to 'selected'."""
    with _lock:
        q = _load_queue()
        recovered = 0
        for jid, job in q["jobs"].items():
            if job.get("status") == "generating":
                job["status"] = "selected"
                job["error"] = None
                recovered += 1
        # Mark any running runs as interrupted
        for r in q.get("runs", []):
            if r.get("status") == "running":
                r["status"] = "interrupted"
                r["completed_at"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S")
        if recovered:
            _save_queue(q)
            logger.info("Recovered %d interrupted jobs back to selected", recovered)


# ---------------------------------------------------------------------------
# Cover letter & LinkedIn message
# ---------------------------------------------------------------------------

def _synthetic_parsed_jd_from_output(job: dict, out_dir) -> dict:
    """Build a minimal parsed_jd dict from output folder artifacts when JD text is unavailable."""
    import json as _json
    from pathlib import Path as _Path

    research_brief = {}
    rb_path = _Path(out_dir) / "research_brief.json"
    if rb_path.exists():
        with open(rb_path) as f:
            research_brief = _json.load(f)

    kw_coverage = {}
    kw_path = _Path(out_dir) / "keyword_coverage.json"
    if kw_path.exists():
        with open(kw_path) as f:
            kw_coverage = _json.load(f)

    # Reconstruct responsibilities from research brief emphasis areas
    responsibilities = research_brief.get("emphasis_areas", [])

    # P0/P1 keywords from keyword coverage counts
    p0_keywords = list((kw_coverage.get("p0_counts") or {}).keys())
    p1_keywords = list((kw_coverage.get("p1_counts") or {}).keys())
    all_keywords = p0_keywords + p1_keywords

    return {
        "job_title": job.get("title", "Director of Product Management"),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "key_responsibilities": responsibilities,
        "company_context": research_brief.get("role_purpose", ""),
        "achievement_language": research_brief.get("emphasis_areas", []),
        "p0_keywords": p0_keywords,
        "p1_keywords": p1_keywords,
        "p2_keywords": [],
        "all_keywords_flat": all_keywords,
        "cultural_signals": [],
        "job_level": "Director",
    }


def generate_cover_letter_for_job(job_id: str) -> dict:
    """Generate a cover letter for a job that has a ready resume."""
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job:
            return {"status": "error", "error": "Job not found"}
        if job.get("status") not in ("ready", "applied"):
            return {"status": "error", "error": "Resume must be ready first"}
        jd_text = job.get("description", "")
        output_folder = job.get("output_folder", "")

    if not output_folder:
        return {"status": "error", "error": "No output folder"}

    try:
        from engine.cover_letter import generate_cover_letter
        from engine.jd_parser import parse_jd
        import json as _json
        from pathlib import Path as _Path

        # Load PKB
        pkb_path = DATA_DIR / "pkb.json"
        pkb = {}
        if pkb_path.exists():
            with open(pkb_path) as f:
                pkb = _json.load(f)

        # Parse JD — fall back to synthetic parsed_jd from output folder artifacts
        # when JD text was not stored at queue time
        if jd_text:
            parsed_jd = parse_jd(jd_text)
        else:
            logger.info("No JD text for %s — building synthetic parsed_jd from output folder", job_id)
            parsed_jd = _synthetic_parsed_jd_from_output(job, _Path(output_folder))

        # Load resume content from reframing_log or formatted content
        resume_content = {}
        reframing_path = _Path(output_folder) / "reframing_log.json"
        if reframing_path.exists():
            with open(reframing_path) as f:
                resume_content = {"reframing_log": _json.load(f)}

        # Load research brief if available
        research_brief = None
        research_path = _Path(output_folder) / "research_brief.json"
        if research_path.exists():
            with open(research_path) as f:
                research_brief = _json.load(f)

        result = generate_cover_letter(parsed_jd, pkb, resume_content, research_brief)

        # Save cover letter
        out_dir = _Path(output_folder)
        with open(out_dir / "cover_letter.txt", "w") as f:
            f.write(result.get("text", ""))

        # Save LinkedIn message if generated in same call
        linkedin_text = result.get("linkedin_text", "")
        if linkedin_text:
            with open(out_dir / "linkedin_message.txt", "w") as f:
                f.write(linkedin_text)

        with _lock:
            q = _load_queue()
            j = q["jobs"].get(job_id, {})
            j["has_cover_letter"] = True
            if linkedin_text:
                j["has_linkedin_message"] = True
            _save_queue(q)

        return {
            "status": "ok",
            "text": result.get("text", ""),
            "linkedin_text": linkedin_text,
        }
    except Exception as e:
        logger.exception("Cover letter generation failed for %s: %s", job_id, e)
        return {"status": "error", "error": str(e)}


def generate_linkedin_message_for_job(job_id: str, message_type: str = "connection_request") -> dict:
    """Generate a LinkedIn message for a job that has a ready resume."""
    with _lock:
        q = _load_queue()
        job = q["jobs"].get(job_id)
        if not job:
            return {"status": "error", "error": "Job not found"}
        if job.get("status") not in ("ready", "applied"):
            return {"status": "error", "error": "Resume must be ready first"}
        jd_text = job.get("description", "")
        output_folder = job.get("output_folder", "")

    if not jd_text:
        return {"status": "error", "error": "No JD text available"}

    try:
        from engine.linkedin_message import generate_linkedin_message
        from engine.jd_parser import parse_jd
        import json as _json
        from pathlib import Path as _Path

        pkb_path = DATA_DIR / "pkb.json"
        pkb = {}
        if pkb_path.exists():
            with open(pkb_path) as f:
                pkb = _json.load(f)

        parsed_jd = parse_jd(jd_text)
        resume_content = {}

        result = generate_linkedin_message(parsed_jd, pkb, resume_content, message_type)

        # Save to output folder if available
        if output_folder:
            out_dir = _Path(output_folder)
            with open(out_dir / "linkedin_message.txt", "w") as f:
                f.write(result.get("text", ""))

        with _lock:
            q = _load_queue()
            j = q["jobs"].get(job_id, {})
            j["has_linkedin_message"] = True
            _save_queue(q)

        return {"status": "ok", "text": result.get("text", ""), "message_type": message_type}
    except Exception as e:
        logger.exception("LinkedIn message generation failed for %s: %s", job_id, e)
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Threaded operations (for web routes)
# ---------------------------------------------------------------------------

def is_search_active() -> bool:
    global _active_search_thread
    return _active_search_thread is not None and _active_search_thread.is_alive()


def is_generate_active() -> bool:
    global _active_generate_thread
    return _active_generate_thread is not None and _active_generate_thread.is_alive()


def is_run_active() -> bool:
    return is_search_active() or is_generate_active()


def start_search_thread(
    progress_cb=None,
    date_posted: str = "week",
    num_pages: int = 1,
    min_score: int = 65,
    sort_by: str = "score",
) -> bool:
    """Start a search-only run in a daemon thread. Returns False if already running."""
    global _active_search_thread
    with _lock:
        if is_search_active() or is_generate_active():
            return False

        def _run():
            try:
                run_search_only(
                    progress_cb=progress_cb,
                    date_posted=date_posted,
                    num_pages=num_pages,
                    min_score=min_score,
                    sort_by=sort_by,
                )
            except Exception:
                logger.exception("Search thread crashed")

        _active_search_thread = threading.Thread(target=_run, daemon=True)
        _active_search_thread.start()
    return True


def start_generate_thread(progress_cb=None) -> bool:
    """Start generation for selected jobs in a daemon thread. Returns False if already running."""
    global _active_generate_thread
    with _lock:
        if is_search_active() or is_generate_active():
            return False

        def _run():
            try:
                run_generation_for_selected(progress_cb=progress_cb)
            except Exception:
                logger.exception("Generate thread crashed")

        _active_generate_thread = threading.Thread(target=_run, daemon=True)
        _active_generate_thread.start()
    return True


def start_single_generate_thread(job_id: str, progress_cb=None) -> bool:
    """Start generation for a single job in a daemon thread."""
    global _active_generate_thread
    with _lock:
        if is_generate_active():
            return False

        def _run():
            try:
                generate_single_resume(job_id, progress_cb=progress_cb)
            except Exception:
                logger.exception("Single generate thread crashed")

        _active_generate_thread = threading.Thread(target=_run, daemon=True)
        _active_generate_thread.start()
    return True
