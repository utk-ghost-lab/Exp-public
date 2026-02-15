"""
Phase 2 Component 5: Daily Shortlist — CLI Output + Phase 1 Integration

Wires: searcher → fetcher → parser → scorer → ranked markdown output.
Each shortlisted job includes a `python main.py --jd-url` command for Phase 1 handoff.

Usage:
    python researcher/daily_shortlist.py [--skip-search] [--urls URL1 URL2 ...]
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from researcher.job_searcher import search_all_sources, load_seen_jobs, search_urls
from researcher.jd_fetcher import fetch_full_jd
from researcher.job_scorer import score_job, load_pkb, _build_candidate_skills, _build_candidate_domains
from researcher.company_analyzer import load_watchlist, estimate_salary_signal

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RESEARCH_DIR = os.path.join(DATA_DIR, "research")


def _generate_why_this_fits(job: dict, pkb: dict) -> str:
    """Generate a 2-sentence recruiter-style summary of why a job fits.

    Uses Claude Haiku for cost efficiency (~$0.002 per call).
    Returns empty string on failure.
    """
    try:
        import anthropic
        from engine.api_utils import messages_create_with_retry

        sc = job.get("score", {})
        components = sc.get("components", {})
        missing = sc.get("missing_critical_skills", [])
        parsed = job.get("parsed_jd", {})

        # Build concise context
        matched_domains = components.get("domain_match", {}).get("details", "")
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        responsibilities = parsed.get("key_responsibilities", [])[:3]
        fit_score = sc.get("fit_score", 0)

        # PKB summary
        pkb_roles = []
        for exp in pkb.get("work_experience", [])[:3]:
            pkb_roles.append(f"{exp.get('title', '')} at {exp.get('company', '')}")
        pkb_summary = "; ".join(pkb_roles) if pkb_roles else "experienced PM"

        prompt = (
            f"You are a recruiting coordinator writing an internal note about a candidate.\n"
            f"Job: {title} at {company}\n"
            f"Key responsibilities: {', '.join(responsibilities)}\n"
            f"Candidate background: {pkb_summary}\n"
            f"Fit score: {fit_score}%\n"
            f"Missing skills: {', '.join(missing) if missing else 'None'}\n\n"
            f"Write exactly 2 sentences: (1) why this candidate is a strong match for this role, "
            f"(2) the main gap they should prepare to address in the interview. "
            f"Be specific. No fluff. Sound like a recruiter's internal Slack message."
        )

        client = anthropic.Anthropic()
        resp = messages_create_with_retry(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    except Exception as e:
        logger.warning(f"Why-this-fits generation failed for {job.get('company')}: {e}")
        return ""


def _load_criteria() -> dict:
    """Load search criteria from config file."""
    path = os.path.join(DATA_DIR, "job_criteria.json")
    with open(path) as f:
        return json.load(f)


def _parse_jd_safe(jd_text: str) -> dict:
    """Parse a JD text, returning None on failure."""
    if not jd_text or len(jd_text) < 50:
        return None
    try:
        from engine.jd_parser import parse_jd
        from engine.jd_cache import get_cached_parsed_jd, set_cached_parsed_jd

        # Check cache first
        cached = get_cached_parsed_jd(jd_text)
        if cached:
            return cached

        parsed = parse_jd(jd_text)
        set_cached_parsed_jd(jd_text, parsed)
        return parsed
    except Exception as e:
        logger.warning(f"JD parse failed: {e}")
        return None


def run_shortlist(skip_search: bool = False, urls: list = None,
                  progress_cb=None) -> dict:
    """Run the full discovery → score pipeline.

    Args:
        skip_search: If True, skip board searches (only process URLs).
        urls: Optional list of specific URLs to score.
        progress_cb: Optional callback(step, message).

    Returns:
        Dict with {jobs, stats, shortlist_path}.
    """
    pkb = load_pkb()
    candidate_skills = _build_candidate_skills(pkb)
    candidate_domains = _build_candidate_domains(pkb)

    all_jobs = []

    # Step 1: Discover jobs
    if not skip_search:
        if progress_cb:
            progress_cb("search", "Searching job boards...")
        criteria = _load_criteria()
        board_jobs = search_all_sources(
            criteria,
            progress_cb=lambda src, status, count=0: (
                progress_cb("search", f"[{src}] {status}") if progress_cb else None
            ),
        )
        all_jobs.extend(board_jobs)
        if progress_cb:
            progress_cb("search", f"Found {len(board_jobs)} new jobs from boards")

    # Step 1b: Process user-supplied URLs
    if urls:
        if progress_cb:
            progress_cb("urls", f"Fetching {len(urls)} URLs...")
        url_jobs = search_urls(
            urls,
            progress_cb=lambda src, status, count=0: (
                progress_cb("urls", f"[{src}] {status}") if progress_cb else None
            ),
        )
        all_jobs.extend(url_jobs)

    if progress_cb:
        progress_cb("fetch", f"Fetching full JD text for {len(all_jobs)} jobs...")

    # Step 2: Fetch full JD text
    for i, job in enumerate(all_jobs):
        if not job.get("description") or len(job.get("description", "")) < 300:
            jd_text = fetch_full_jd(job)
            job["description"] = jd_text
        if progress_cb and (i + 1) % 5 == 0:
            progress_cb("fetch", f"Fetched {i+1}/{len(all_jobs)} JDs")

    # Step 3: Parse JDs
    if progress_cb:
        progress_cb("parse", f"Parsing {len(all_jobs)} JDs...")

    parsed_count = 0
    for job in all_jobs:
        desc = job.get("description", "")
        if desc:
            parsed = _parse_jd_safe(desc)
            if parsed:
                job["parsed_jd"] = parsed
                # Extract title/company from parsed JD if missing
                if not job.get("title"):
                    job["title"] = parsed.get("job_title", "Unknown")
                if not job.get("company"):
                    job["company"] = parsed.get("company", "Unknown")
                parsed_count += 1

    if progress_cb:
        progress_cb("parse", f"Parsed {parsed_count}/{len(all_jobs)} JDs successfully")

    # Step 4: Score all jobs
    if progress_cb:
        progress_cb("score", f"Scoring {parsed_count} jobs...")

    scored_jobs = []
    for job in all_jobs:
        if job.get("parsed_jd"):
            result = score_job(
                job["parsed_jd"], pkb, job.get("posted_days_ago"),
                candidate_skills=candidate_skills,
                candidate_domains=candidate_domains,
            )
            job["score"] = result
            scored_jobs.append(job)

    # Sort by fit score descending
    scored_jobs.sort(key=lambda j: j["score"]["fit_score"], reverse=True)

    if progress_cb:
        progress_cb("score", f"Scored {len(scored_jobs)} jobs")

    # Step 4b: Salary enrichment for top-tier jobs only
    top_tier_jobs = [
        j for j in scored_jobs
        if j["score"]["recommendation"] in ("APPLY TODAY", "WORTH TRYING")
    ]
    if top_tier_jobs:
        if progress_cb:
            progress_cb("salary", f"Estimating salary for {len(top_tier_jobs)} top jobs...")
        for job in top_tier_jobs:
            try:
                sig = estimate_salary_signal(
                    job.get("company", ""), job.get("location", "")
                )
                job["salary_signal"] = sig
            except Exception as e:
                logger.warning(f"Salary estimation failed for {job.get('company')}: {e}")
            time.sleep(0.5)  # Polite delay

    # Step 4c: "Why This Fits" for top 5 APPLY TODAY jobs
    apply_today = [
        j for j in scored_jobs
        if j["score"]["recommendation"] == "APPLY TODAY"
    ][:5]
    if apply_today:
        if progress_cb:
            progress_cb("why_fits", f"Generating fit summaries for {len(apply_today)} top jobs...")
        for job in apply_today:
            why = _generate_why_this_fits(job, pkb)
            if why:
                job["why_this_fits"] = why

    # Step 5: Generate shortlist
    stats = _compute_stats(all_jobs, scored_jobs)

    # Step 6: Write markdown report
    today = datetime.now().strftime("%Y-%m-%d")
    shortlist_path = os.path.join(RESEARCH_DIR, f"shortlist_{today}.md")
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    md = _generate_markdown(scored_jobs, stats, today)
    with open(shortlist_path, "w") as f:
        f.write(md)

    # Also save raw JSON
    json_path = os.path.join(RESEARCH_DIR, f"shortlist_{today}.json")
    _save_results_json(scored_jobs, stats, json_path)

    if progress_cb:
        progress_cb("done", f"Shortlist saved to {shortlist_path}")

    return {
        "jobs": scored_jobs,
        "stats": stats,
        "shortlist_path": shortlist_path,
    }


def _compute_stats(all_jobs: list, scored_jobs: list) -> dict:
    """Compute summary statistics."""
    tiers = {"APPLY TODAY": 0, "WORTH TRYING": 0, "STRETCH": 0, "SKIP": 0}
    for job in scored_jobs:
        rec = job["score"]["recommendation"]
        tiers[rec] = tiers.get(rec, 0) + 1

    return {
        "total_scanned": len(all_jobs),
        "total_scored": len(scored_jobs),
        "tiers": tiers,
        "sources": _count_by_key(all_jobs, "source"),
    }


def _count_by_key(jobs: list, key: str) -> dict:
    """Count jobs grouped by a key field."""
    counts = {}
    for j in jobs:
        val = j.get(key, "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts


def _generate_markdown(scored_jobs: list, stats: dict, date: str) -> str:
    """Generate daily shortlist markdown report."""
    tiers = stats["tiers"]
    lines = [
        f"# Daily Job Shortlist — {date}",
        f"Scanned: {stats['total_scanned']} jobs | "
        f"Scored: {stats['total_scored']} | "
        f"Apply Today: {tiers.get('APPLY TODAY', 0)} | "
        f"Worth Trying: {tiers.get('WORTH TRYING', 0)} | "
        f"Stretch: {tiers.get('STRETCH', 0)}",
        "",
    ]

    # Group by tier
    tier_order = ["APPLY TODAY", "WORTH TRYING", "STRETCH"]
    for tier in tier_order:
        tier_jobs = [j for j in scored_jobs if j["score"]["recommendation"] == tier]
        if not tier_jobs:
            continue

        emoji = {"APPLY TODAY": "🟢", "WORTH TRYING": "🟡", "STRETCH": "🟠"}.get(tier, "")
        lines.append(f"\n## {emoji} {tier} ({len(tier_jobs)} roles)\n")

        for i, job in enumerate(tier_jobs, 1):
            sc = job["score"]
            title = job.get("title", "Unknown")
            company = job.get("company", "Unknown")
            location = job.get("location", "")
            url = job.get("job_url", "")
            source = job.get("source", "")

            lines.append(f"### {i}. {title} — {company}")
            loc_str = f" | {location}" if location else ""
            lines.append(f"**{sc['fit_score']}% fit**{loc_str} | Source: {source}")

            # Component breakdown
            components = sc.get("components", {})
            parts = []
            for name, comp in components.items():
                parts.append(f"{name.replace('_', ' ').title()}: {comp['score']}/{comp['max']}")
            lines.append(f"Breakdown: {' | '.join(parts)}")

            # Missing skills
            missing = sc.get("missing_critical_skills", [])
            if missing:
                lines.append(f"Missing P0: {', '.join(missing)}")

            # Salary signal
            salary = job.get("salary_signal")
            if salary:
                sal_line = f"Salary ({salary.get('region', 'unknown')}): "
                if salary.get("estimated_range"):
                    sal_line += f"~{salary['estimated_range']}"
                else:
                    sal_line += "No data found"
                sal_line += f" (benchmark: {salary.get('benchmark', 'N/A')})"
                if salary.get("below_target"):
                    sal_line += " ⚠️ BELOW TARGET"
                lines.append(sal_line)

            # Why this fits
            why_fits = job.get("why_this_fits", "")
            if why_fits:
                lines.append(f"> {why_fits}")

            # Phase 1 command
            if url:
                lines.append(f"```")
                lines.append(f'python main.py --jd-url "{url}" --company "{company}"')
                lines.append(f"```")
                lines.append(f"[View JD]({url})")
            lines.append("")

    # Watchlist alerts
    watchlist = load_watchlist()

    # Hiring spike alerts (top priority)
    spike_alerts = []
    for name, info in watchlist.get("companies", {}).items():
        spike = info.get("hiring_spike", {})
        if spike.get("spike"):
            spike_alerts.append(
                f"- **{name}** — {spike.get('current', 0)} PM roles now "
                f"(avg {spike.get('avg_30d', 0):.0f} in prior 30 days). "
                f"{spike.get('reason', '')}"
            )

    if spike_alerts:
        lines.append("\n## HIRING SPIKE ALERTS\n")
        lines.extend(spike_alerts)
        lines.append("")

    alerts = []
    for name, info in watchlist.get("companies", {}).items():
        # Skip companies already in spike alerts
        if info.get("hiring_spike", {}).get("spike"):
            continue
        pm_count = info.get("pm_roles_30d", 0)
        if pm_count >= 2:
            alerts.append(f"- **{name}** has {pm_count} PM roles on career page")
        if info.get("linkedin_signal"):
            alerts.append(f"- **{name}** has LinkedIn hiring signals")

    if alerts:
        lines.append("\n## Watchlist Alerts\n")
        lines.extend(alerts)
        lines.append("")

    # Sources breakdown
    lines.append("\n---")
    lines.append(f"*Sources: {', '.join(f'{s}: {c}' for s, c in stats['sources'].items())}*")

    return "\n".join(lines)


def _save_results_json(scored_jobs: list, stats: dict, path: str):
    """Save scored results as JSON for web UI consumption."""
    results = []
    for job in scored_jobs:
        entry = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "job_url": job.get("job_url", ""),
            "source": job.get("source", ""),
            "posted_days_ago": job.get("posted_days_ago"),
            "score": job.get("score", {}),
            "description_length": len(job.get("description", "")),
        }
        if job.get("salary_signal"):
            entry["salary_signal"] = job["salary_signal"]
        if job.get("why_this_fits"):
            entry["why_this_fits"] = job["why_this_fits"]
        results.append(entry)

    data = {"stats": stats, "jobs": results, "generated_at": datetime.now().isoformat()}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ===================================================================
# CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="Daily Job Shortlist Generator")
    parser.add_argument("--skip-search", action="store_true",
                        help="Skip board searches (only process URLs)")
    parser.add_argument("--urls", nargs="*", default=[],
                        help="Specific job URLs to score")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    def progress(step, message):
        print(f"  [{step}] {message}")

    print("=" * 60)
    print("  DAILY JOB SHORTLIST GENERATOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    result = run_shortlist(
        skip_search=args.skip_search,
        urls=args.urls or None,
        progress_cb=progress,
    )

    stats = result["stats"]
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"  Scanned: {stats['total_scanned']} | Scored: {stats['total_scored']}")
    for tier, count in stats["tiers"].items():
        if count > 0:
            print(f"  {tier}: {count}")
    print(f"\n  Shortlist saved: {result['shortlist_path']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
