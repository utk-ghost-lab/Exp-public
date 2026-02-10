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
    python main.py --jd-url "https://example.com/job-posting"
    python main.py --build-profile  (one-time PKB setup)
"""

import argparse
import json
import logging
import os
import sys

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


def run_pipeline(jd_text: str):
    """Run the full resume generation pipeline for a given JD."""
    from engine.jd_parser import parse_jd
    from engine.profile_mapper import map_profile_to_jd
    from engine.reframer import reframe_experience
    from engine.keyword_optimizer import optimize_keywords
    from engine.formatter import format_for_ats
    from engine.scorer import score_resume
    from engine.generator import generate_output

    # Load PKB
    pkb_path = "data/pkb.json"
    if not os.path.exists(pkb_path):
        logger.error("PKB not found. Run with --build-profile first.")
        sys.exit(1)

    with open(pkb_path, "r") as f:
        pkb = json.load(f)

    # Step 1: Parse JD
    logger.info("Step 1: Parsing job description...")
    parsed_jd = parse_jd(jd_text)

    # Step 2: Map profile to JD
    logger.info("Step 2: Mapping profile to JD requirements...")
    mapping = map_profile_to_jd(parsed_jd, pkb)

    # Step 3: Reframe experience
    logger.info("Step 3: Generating tailored resume content...")
    resume_content = reframe_experience(mapping, pkb, parsed_jd)
    reframing_log = resume_content.get("reframing_log", [])

    # Step 4: Optimize keywords
    logger.info("Step 4: Optimizing keyword density...")
    optimized = optimize_keywords(resume_content, parsed_jd)
    resume_content = optimized["optimized_content"]
    keyword_report = optimized["keyword_report"]

    # Step 5: Format for ATS
    logger.info("Step 5: Applying ATS formatting...")
    resume_content = format_for_ats(resume_content)

    # Step 6: Score and iterate
    logger.info("Step 6: Scoring resume...")
    max_iterations = 3
    for iteration in range(max_iterations):
        score_report = score_resume(resume_content, parsed_jd)
        total_score = score_report["total_score"]
        logger.info(f"  Iteration {iteration + 1}: Score = {total_score}")

        if total_score >= 90:
            break
        elif total_score >= 80:
            # One optimization pass on lowest component
            logger.info("  Score 80-89, running optimization pass...")
            optimized = optimize_keywords(resume_content, parsed_jd)
            resume_content = optimized["optimized_content"]
            break
        else:
            # Re-run Steps 3-5
            logger.info("  Score < 80, re-running Steps 3-5...")
            resume_content = reframe_experience(mapping, pkb, parsed_jd)
            optimized = optimize_keywords(resume_content, parsed_jd)
            resume_content = optimized["optimized_content"]
            resume_content = format_for_ats(resume_content)

    # Step 7: Generate output
    logger.info("Step 7: Generating final output...")
    output_path = generate_output(
        resume_content, score_report, keyword_report, reframing_log, parsed_jd
    )
    logger.info(f"Resume package saved to: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Placement Team — Resume Engine")
    parser.add_argument("--jd", type=str, help="Job description text")
    parser.add_argument("--jd-url", type=str, help="URL to job posting")
    parser.add_argument(
        "--build-profile", action="store_true", help="Build Profile Knowledge Base"
    )

    args = parser.parse_args()

    if args.build_profile:
        build_profile()
    elif args.jd:
        run_pipeline(args.jd)
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
