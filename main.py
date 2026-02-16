"""Placement Team — Resume Engine Orchestrator

Runs the full 7-step pipeline:
  1. JD Deep Parse
  2. Profile KB Query
  3. Intelligent Reframing
  4. Keyword Density Optimization
  5. ATS Format Compliance
  6. Self-Scoring (with iteration)
  7. Final Output Generation

Usage:
    python main.py --jd "paste job description here"
    python main.py --jd-file path/to/jd.txt
    python main.py --build-profile  (one-time PKB setup)
    python main.py --jd-file jd.txt --review  (show JSON before PDF, allow edit; edits are logged for next run)
"""

import argparse
import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("placement-team")


def build_profile():
    """One-time: Build the Profile Knowledge Base from profile/ documents."""
    from engine.profile_builder import build_pkb

    logger.info("Building Profile Knowledge Base...")
    pkb = build_pkb()
    logger.info("PKB built and saved to data/pkb.json")
    return pkb


def run_pipeline(
    jd_text: str,
    review: bool = False,
    fast: bool = False,
    fast_no_improve: bool = False,
    combined_parse_map: bool = False,
    use_cache: bool = True,
    progress_callback=None,
    stop_before_pdf: bool = False,
    enable_research: bool = False,
):
    """Run the full resume generation pipeline for a given JD.

    Args:
        jd_text: Raw job description text.
        review: If True, after Step 6 show resume JSON in $EDITOR for user to edit before PDF; record edits.
        fast: If True, at most one scoring iteration (max_iterations=1).
        fast_no_improve: If True, score once and never call reframer for patch (output as-is when score < 90).
        combined_parse_map: If True, run Step 1+2 in a single API call.
        use_cache: If True, use cached parsed_jd and mapping when available.
        progress_callback: Optional callable(step_number, status, message, data) called at end of each step.
        stop_before_pdf: If True, stop after Step 6 and return state dict (no Step 7). For web UI.

    Returns:
        When stop_before_pdf is False: output folder path (str).
        When stop_before_pdf is True: dict with resume_content, parsed_jd, score_report, keyword_report,
            reframing_log, format_validation, iteration_log, pkb.
    """
    def _progress(step: int, status: str, message: str, data: dict = None):
        if progress_callback:
            progress_callback(step, status, message, data or {})
    from engine.jd_parser import parse_jd
    from engine.profile_mapper import map_profile_to_jd
    from engine.reframer import reframe_experience
    from engine.keyword_optimizer import optimize_keywords
    from engine.formatter import format_resume
    from engine.scorer import run_scoring_with_iteration
    from engine.generator import generate_output, QualityGateBlockedError
    from engine.review_edit import offer_edit_and_apply, save_edit_record, append_human_edit_log
    from engine.edit_preferences import get_user_preferences_block
    from engine.jd_cache import (
        get_cached_parsed_jd,
        set_cached_parsed_jd,
        get_cached_mapping,
        set_cached_mapping,
    )

    # Load PKB
    pkb_path = "data/pkb.json"
    if not os.path.exists(pkb_path):
        logger.error("PKB not found. Run with --build-profile first.")
        raise FileNotFoundError("PKB not found. Run with --build-profile first.")

    with open(pkb_path, "r") as f:
        pkb = json.load(f)

    pipeline_start = time.time()

    # Step 1: Parse JD
    if combined_parse_map:
        from engine.jd_parse_and_map import parse_jd_and_map
        t0 = time.time()
        logger.info("Step 1+2: Parsing JD and mapping profile (combined)...")
        parsed_jd, mapping = parse_jd_and_map(jd_text, pkb, pkb_path)
        if use_cache:
            set_cached_parsed_jd(jd_text, parsed_jd)
            set_cached_mapping(jd_text, pkb_path, mapping)
        cov = (mapping.get("coverage_summary") or {})
        _progress(1, "done", "JD parsed and profile mapped", {"p0_count": len(parsed_jd.get("p0_keywords", [])), "p1_count": len(parsed_jd.get("p1_keywords", [])), "p0_covered": cov.get("p0_covered"), "p0_total": cov.get("p0_total")})
        logger.info("  Step 1+2 done in %.1fs", time.time() - t0)
    else:
        # Step 1: Parse JD (from cache or API)
        t0 = time.time()
        logger.info("Step 1: Parsing job description...")
        if use_cache:
            parsed_jd = get_cached_parsed_jd(jd_text)
        else:
            parsed_jd = None
        if parsed_jd is None:
            parsed_jd = parse_jd(jd_text)
            if use_cache:
                set_cached_parsed_jd(jd_text, parsed_jd)
        _progress(1, "done", "JD parsed", {"p0_count": len(parsed_jd.get("p0_keywords", [])), "p1_count": len(parsed_jd.get("p1_keywords", []))})
        logger.info("  Step 1 done in %.1fs", time.time() - t0)

    # Step 1.5: Company Research & Fit Analysis (optional, runs after JD parse, before mapping)
    research_brief = None
    if enable_research:
        t0 = time.time()
        logger.info("Step 1.5: Running company research & fit analysis...")
        _progress(1, "running", "Researching company...")
        try:
            from engine.research_integration import run_company_research
            research_brief = run_company_research(parsed_jd, pkb)
            if research_brief:
                fit = research_brief.get("fit_score_result") or {}
                _progress(1, "done", "Company research complete", {
                    "fit_score": fit.get("fit_score"),
                    "hiring_mode": research_brief.get("hiring_mode"),
                })
                logger.info("  Step 1.5 done in %.1fs (fit_score=%.1f, hiring_mode=%s)",
                            time.time() - t0,
                            fit.get("fit_score", 0),
                            research_brief.get("hiring_mode", "unknown"))
            else:
                logger.info("  Step 1.5 done in %.1fs (no brief produced)", time.time() - t0)
        except Exception as e:
            logger.warning("Step 1.5 failed (non-fatal): %s", e)
            research_brief = None

    # Step 2: Map profile to JD (skip if already done in combined mode)
    if not combined_parse_map:
        t0 = time.time()
        logger.info("Step 2: Mapping profile to JD requirements...")
        if use_cache and not research_brief:
            mapping = get_cached_mapping(jd_text, pkb_path)
        else:
            mapping = None
        if mapping is None:
            mapping = map_profile_to_jd(parsed_jd, pkb, research_brief=research_brief)
            if use_cache:
                set_cached_mapping(jd_text, pkb_path, mapping)
        cov = (mapping.get("coverage_summary") or {})
        _progress(2, "done", "Profile mapped", {"p0_covered": cov.get("p0_covered"), "p0_total": cov.get("p0_total"), "direct": cov.get("direct_count"), "gap": cov.get("gap_count")})
        logger.info("  Step 2 done in %.1fs", time.time() - t0)

    # User preferences from past edits (for reframer)
    user_preferences = get_user_preferences_block()

    # Step 3: Reframe experience from PKB only (no prior resume)
    t0 = time.time()
    logger.info("Step 3: Generating tailored resume content from PKB...")
    resume_content = reframe_experience(
        mapping, pkb, parsed_jd,
        user_preferences_from_edits=user_preferences,
        research_brief=research_brief,
    )
    reframing_log = resume_content.get("reframing_log", [])
    work = resume_content.get("work_experience") or []
    _progress(3, "done", "Resume reframed", {"roles": len(work), "bullets": sum(len(r.get("bullets") or []) for r in work)})
    logger.info("  Step 3 done in %.1fs", time.time() - t0)

    # Step 4: Optimize keywords
    t0 = time.time()
    logger.info("Step 4: Optimizing keyword density...")
    optimized = optimize_keywords(resume_content, parsed_jd)
    resume_content = optimized["optimized_content"]
    keyword_report = optimized["keyword_report"]
    kr = keyword_report or {}
    _progress(4, "done", "Keywords optimized", {"p0_coverage": kr.get("p0_coverage_pct"), "p1_coverage": kr.get("p1_coverage_pct")})
    logger.info("  Step 4 done in %.1fs", time.time() - t0)

    # Step 5: Format validation
    t0 = time.time()
    logger.info("Step 5: Validating format rules...")
    formatted = format_resume(resume_content, parsed_jd)
    format_validation = formatted["format_validation"]
    resume_content = formatted["validated_content"]
    logger.info("  Format status: %s (%d errors, %d warnings)",
                format_validation["status"],
                len(format_validation.get("errors", [])),
                len(format_validation.get("warnings", [])))
    _progress(5, "done", "Format validated", {"status": format_validation.get("status")})
    logger.info("  Step 5 done in %.1fs", time.time() - t0)

    # Step 6: Score and iterate (uses patch mode for re-runs; first resume is always from PKB)
    max_iterations = 1 if fast else 2
    skip_patch_improvement = fast_no_improve
    t0 = time.time()
    logger.info("Step 6: Scoring resume and iterating if below 90...")
    result = run_scoring_with_iteration(
        resume_content, parsed_jd, mapping, pkb,
        max_iterations=max_iterations,
        user_preferences_from_edits=user_preferences,
        skip_patch_improvement=skip_patch_improvement,
    )
    score_report = result["score_report"]
    keyword_report = result["keyword_report"]
    resume_content = result["resume_content"]
    iteration_log = {
        "iterations_used": result.get("iterations_used", 1),
        "feedback_applied": result.get("feedback_applied", []),
        "passed": result.get("passed", False),
    }
    _progress(6, "done", "Scoring complete", {"score": score_report["total_score"], "iterations": iteration_log["iterations_used"]})
    logger.info("  Final score: %.1f (target 90)", score_report["total_score"])
    logger.info("  Step 6 done in %.1fs (%d iterations)", time.time() - t0, iteration_log["iterations_used"])

    if stop_before_pdf:
        return {
            "resume_content": resume_content,
            "parsed_jd": parsed_jd,
            "score_report": score_report,
            "keyword_report": keyword_report,
            "reframing_log": reframing_log,
            "format_validation": format_validation,
            "iteration_log": iteration_log,
            "pkb": pkb,
            "research_brief": research_brief,
        }

    # Step 6.5 (optional): Review and edit JSON before PDF
    edit_record = None
    existing_out_folder = None
    if review:
        import re
        company_name = (parsed_jd.get("company") or "Company").strip()
        company_slug = re.sub(r"[^\w]+", "_", company_name).strip("_")
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        existing_out_folder = os.path.join("output", f"{company_slug}_{date_str}")
        os.makedirs(existing_out_folder, exist_ok=True)
        resume_content, edit_record = offer_edit_and_apply(
            resume_content, parsed_jd, "output", company_slug, date_str,
        )
        if edit_record:
            save_edit_record(edit_record, existing_out_folder)
            append_human_edit_log(edit_record)
            logger.info("Edits recorded for future runs.")

    # Step 7: Generate output (PDF + DOCX + artifacts)
    t0 = time.time()
    logger.info("Step 7: Generating final output package...")
    try:
        output_path = generate_output(
            formatted_content=resume_content,
            jd_analysis=parsed_jd,
            score_report=score_report,
            keyword_report=keyword_report,
            reframing_log=reframing_log,
            format_validation=format_validation,
            iteration_log=iteration_log,
            pkb=pkb,
            edit_record=edit_record,
            existing_out_folder=existing_out_folder,
            research_brief=research_brief,
        )
    except QualityGateBlockedError as e:
        logger.error("Quality gate blocked PDF: %s", e.blocked_reason)
        _progress(7, "blocked", "Quality check failed", {"blocked_reason": e.blocked_reason, "rule13_failures": e.rule13_failures})
        return {
            "blocked": True,
            "blocked_reason": e.blocked_reason,
            "rule13_failures": e.rule13_failures,
        }
    _progress(7, "done", "PDF and artifacts generated", {"output_path": output_path})
    logger.info("  Step 7 done in %.1fs", time.time() - t0)

    total = time.time() - pipeline_start
    logger.info("=" * 50)
    logger.info("TOTAL PIPELINE TIME: %.1fs (%.1f minutes)", total, total / 60)
    logger.info("=" * 50)
    logger.info("Resume package saved to: %s", output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Placement Team — Resume Engine")
    parser.add_argument("--jd", type=str, help="Job description text")
    parser.add_argument("--jd-file", type=str, help="Path to file containing job description text")
    parser.add_argument("--jd-url", type=str, help="URL to job posting")
    parser.add_argument(
        "--build-profile", action="store_true", help="Build Profile Knowledge Base"
    )
    parser.add_argument(
        "--review", action="store_true",
        help="Before PDF: show resume JSON in $EDITOR for edits; record edits for next run",
    )
    parser.add_argument(
        "--fast", action="store_true",
        help="At most one scoring iteration (one patch if score < 90)",
    )
    parser.add_argument(
        "--fast-no-improve", action="store_true",
        help="Score once and output without patch improvement (fastest, may score below 90)",
    )
    parser.add_argument(
        "--combined-parse-map", action="store_true",
        help="Run JD parse and profile mapping in a single API call",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable cache for parsed JD and mapping",
    )
    parser.add_argument(
        "--research", action="store_true",
        help="Enable company research & fit analysis (Step 1.5) for better resume positioning",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose/debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.build_profile:
        build_profile()
    elif args.jd:
        try:
            result = run_pipeline(
                args.jd,
                review=args.review,
                fast=args.fast,
                fast_no_improve=args.fast_no_improve,
                combined_parse_map=args.combined_parse_map,
                use_cache=not args.no_cache,
                enable_research=args.research,
            )
            if isinstance(result, dict) and result.get("blocked"):
                logger.error("Quality check failed. Please try again or contact support.")
                logger.error("Blocked reason: %s", result.get("blocked_reason"))
                logger.error("Rule 13 failures: %s", result.get("rule13_failures", []))
                sys.exit(1)
        except FileNotFoundError as e:
            logger.error("%s", e)
            sys.exit(1)
    elif args.jd_file:
        if not os.path.exists(args.jd_file):
            logger.error("JD file not found: %s", args.jd_file)
            sys.exit(1)
        with open(args.jd_file, "r") as f:
            jd_text = f.read().strip()
        if not jd_text:
            logger.error("JD file is empty: %s", args.jd_file)
            sys.exit(1)
        try:
            result = run_pipeline(
                jd_text,
                review=args.review,
                fast=args.fast,
                fast_no_improve=args.fast_no_improve,
                combined_parse_map=args.combined_parse_map,
                use_cache=not args.no_cache,
                enable_research=args.research,
            )
            if isinstance(result, dict) and result.get("blocked"):
                logger.error("Quality check failed. Please try again or contact support.")
                logger.error("Blocked reason: %s", result.get("blocked_reason"))
                logger.error("Rule 13 failures: %s", result.get("rule13_failures", []))
                sys.exit(1)
        except FileNotFoundError as e:
            logger.error("%s", e)
            sys.exit(1)
    elif args.jd_url:
        from engine.jd_parser import parse_jd_from_url

        logger.info("Fetching JD from URL...")
        # For URL, we'll scrape then run pipeline
        # Implementation will handle this in jd_parser
        logger.error("URL parsing not yet implemented. Paste JD text with --jd instead.")
        sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
