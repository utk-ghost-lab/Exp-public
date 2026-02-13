"""
Placement Team — Test Suite Runner (Step 9)

Runs the full pipeline on 5 diverse JDs, captures intermediate results,
and generates a comparison report.

Usage:
    python tests/run_test_suite.py                    # Run all tests
    python tests/run_test_suite.py --test 1           # Run single test
    python tests/run_test_suite.py --test 1 3 5       # Run specific tests
    python tests/run_test_suite.py --skip-generation  # Score-only (reuse existing output)
"""

import argparse
import copy
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("test-suite")

# --- Test definitions ---
TEST_CASES = {
    1: {
        "name": "Zenoti Vertical SaaS",
        "file": "test1_zenoti_vertical_saas.txt",
        "type": "Vertical SaaS PM",
        "stress_test": "Baseline — direct domain match (beauty/wellness)",
    },
    2: {
        "name": "FAANG Big Tech",
        "file": "test2_faang_big_tech.txt",
        "type": "FAANG / Big Tech PM",
        "stress_test": "Title matching (L5/L6), broad JD with 15+ requirements",
    },
    3: {
        "name": "AI/ML Product Manager",
        "file": "test3_ai_ml_product_manager.txt",
        "type": "AI/ML PM",
        "stress_test": "Deep ML reqs — Rule 7 anachronism check, adjacent reframing",
    },
    4: {
        "name": "Startup Generalist",
        "file": "test4_startup_generalist.txt",
        "type": "Startup PM (Series A-B)",
        "stress_test": "30+ requirements — P0 over-classification, breadth vs depth",
    },
    5: {
        "name": "Zero Overlap Domain",
        "file": "test5_zero_overlap_domain.txt",
        "type": "Healthcare PM",
        "stress_test": "Near-zero domain overlap — transferable/gap handling",
    },
}


def run_single_test(test_id: int, jd_dir: str, results_dir: str) -> dict:
    """Run the full pipeline for a single test JD and capture all intermediate data.

    Returns a results dict with timing, scores, counts, and intermediate artifacts.
    """
    from engine.jd_parser import parse_jd
    from engine.profile_mapper import map_profile_to_jd
    from engine.reframer import reframe_experience
    from engine.keyword_optimizer import optimize_keywords
    from engine.formatter import format_resume
    from engine.scorer import run_scoring_with_iteration
    from engine.generator import generate_output

    tc = TEST_CASES[test_id]
    jd_path = os.path.join(jd_dir, tc["file"])

    if not os.path.exists(jd_path):
        return {"test_id": test_id, "error": f"JD file not found: {jd_path}"}

    with open(jd_path, "r") as f:
        jd_text = f.read().strip()

    if not jd_text:
        return {"test_id": test_id, "error": f"JD file is empty: {jd_path}"}

    # Load PKB
    pkb_path = os.path.join(PROJECT_ROOT, "data", "pkb.json")
    if not os.path.exists(pkb_path):
        return {"test_id": test_id, "error": "PKB not found. Run: python main.py --build-profile"}

    with open(pkb_path, "r") as f:
        pkb = json.load(f)

    result = {
        "test_id": test_id,
        "test_name": tc["name"],
        "test_type": tc["type"],
        "stress_test": tc["stress_test"],
        "timestamp": datetime.now().isoformat(),
        "timings": {},
        "errors": [],
    }

    total_start = time.time()

    # --- Step 1: Parse JD ---
    try:
        logger.info("Test %d: Step 1 — Parsing JD...", test_id)
        t0 = time.time()
        parsed_jd = parse_jd(jd_text)
        result["timings"]["jd_parse"] = round(time.time() - t0, 1)

        result["jd_parser"] = {
            "job_title": parsed_jd.get("job_title", ""),
            "company": parsed_jd.get("company", ""),
            "p0_count": len(parsed_jd.get("p0_keywords") or []),
            "p1_count": len(parsed_jd.get("p1_keywords") or []),
            "p2_count": len(parsed_jd.get("p2_keywords") or []),
            "total_keywords": len(parsed_jd.get("all_keywords_flat") or []),
            "p0_keywords": parsed_jd.get("p0_keywords") or [],
            "responsibilities_count": len(parsed_jd.get("key_responsibilities") or []),
            "job_level": parsed_jd.get("job_level", ""),
        }
    except Exception as e:
        result["errors"].append(f"JD Parse failed: {str(e)}")
        logger.error("Test %d: JD Parse FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 2: Profile Mapping ---
    try:
        logger.info("Test %d: Step 2 — Mapping profile...", test_id)
        t0 = time.time()
        mapping = map_profile_to_jd(parsed_jd, pkb)
        result["timings"]["profile_map"] = round(time.time() - t0, 1)

        mappings = mapping.get("mappings") or []
        match_types = {"DIRECT": 0, "ADJACENT": 0, "TRANSFERABLE": 0, "GAP": 0}
        for m in mappings:
            mt = (m.get("match_type") or "").upper()
            if mt in match_types:
                match_types[mt] += 1

        coverage = mapping.get("coverage_summary") or {}
        result["profile_mapper"] = {
            "match_types": match_types,
            "total_mappings": len(mappings),
            "p0_covered": coverage.get("p0_covered", 0),
            "p0_total": coverage.get("p0_total", 0),
            "p0_coverage_pct": coverage.get("p0_coverage_pct", 0),
            "gaps": coverage.get("gaps") or [],
        }
    except Exception as e:
        result["errors"].append(f"Profile Mapping failed: {str(e)}")
        logger.error("Test %d: Profile Mapping FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 3: Reframing ---
    try:
        logger.info("Test %d: Step 3 — Reframing experience...", test_id)
        t0 = time.time()
        resume_content = reframe_experience(mapping, pkb, parsed_jd)
        # Defensive: if reframer returned a string, try to parse it as JSON
        if isinstance(resume_content, str):
            logger.warning("Test %d: Reframer returned string instead of dict, attempting JSON parse", test_id)
            resume_content = json.loads(resume_content)
        reframing_log = resume_content.get("reframing_log", [])
        result["timings"]["reframe"] = round(time.time() - t0, 1)

        # Count bullets
        total_bullets = 0
        for role in resume_content.get("work_experience") or []:
            total_bullets += len(role.get("bullets") or [])
        result["reframer"] = {
            "total_bullets": total_bullets,
            "roles_count": len(resume_content.get("work_experience") or []),
            "reframing_log_entries": len(reframing_log),
        }
    except Exception as e:
        result["errors"].append(f"Reframing failed: {str(e)}")
        logger.error("Test %d: Reframing FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 4: Keyword Optimization ---
    try:
        logger.info("Test %d: Step 4 — Optimizing keywords...", test_id)
        t0 = time.time()
        optimized = optimize_keywords(resume_content, parsed_jd)
        resume_content = optimized["optimized_content"]
        keyword_report = optimized["keyword_report"]
        result["timings"]["keyword_opt"] = round(time.time() - t0, 1)

        result["keyword_optimizer"] = {
            "p0_coverage": keyword_report.get("p0_coverage_pct", 0),
            "p1_coverage": keyword_report.get("p1_coverage_pct", 0),
            "missing_p0": keyword_report.get("missing_p0") or [],
            "over_used": keyword_report.get("over_used_keywords") or [],
        }
    except Exception as e:
        result["errors"].append(f"Keyword optimization failed: {str(e)}")
        logger.error("Test %d: Keyword Opt FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 5: Format Validation ---
    try:
        logger.info("Test %d: Step 5 — Format validation...", test_id)
        t0 = time.time()
        formatted = format_resume(resume_content, parsed_jd)
        format_validation = formatted["format_validation"]
        resume_content = formatted["validated_content"]
        result["timings"]["format"] = round(time.time() - t0, 1)

        result["formatter"] = {
            "status": format_validation.get("status", ""),
            "errors_count": len(format_validation.get("errors") or []),
            "warnings_count": len(format_validation.get("warnings") or []),
            "auto_fixes": len(format_validation.get("auto_fixes") or []),
            "estimated_pages": format_validation.get("estimated_pages", 0),
            "error_details": format_validation.get("errors") or [],
            "warning_details": format_validation.get("warnings") or [],
        }
    except Exception as e:
        result["errors"].append(f"Formatter failed: {str(e)}")
        logger.error("Test %d: Formatter FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 6: Scoring with Iteration ---
    try:
        logger.info("Test %d: Step 6 — Scoring and iterating...", test_id)
        t0 = time.time()
        score_result = run_scoring_with_iteration(
            resume_content, parsed_jd, mapping, pkb, max_iterations=3
        )
        score_report = score_result["score_report"]
        keyword_report = score_result["keyword_report"]
        resume_content = score_result["resume_content"]
        result["timings"]["scoring"] = round(time.time() - t0, 1)

        components = score_report.get("components") or {}
        result["scorer"] = {
            "total_score": score_report.get("total_score", 0),
            "passed": score_report.get("passed", False),
            "iterations_used": score_result.get("iterations_used", 0),
            "weakest_component": score_report.get("weakest_component", ""),
            "weakest_two": score_report.get("weakest_two") or [],
            "components": {
                k: {"score": v.get("score", 0), "weighted": v.get("weighted", 0)}
                for k, v in components.items()
            },
            "feedback_applied": score_result.get("feedback_applied") or [],
        }
    except Exception as e:
        result["errors"].append(f"Scoring failed: {str(e)}")
        logger.error("Test %d: Scoring FAILED: %s", test_id, e)
        result["timings"]["total"] = round(time.time() - total_start, 1)
        return result

    # --- Step 7: Generate Output ---
    try:
        logger.info("Test %d: Step 7 — Generating output...", test_id)
        t0 = time.time()
        test_output_dir = os.path.join(results_dir, f"test{test_id}")
        os.makedirs(test_output_dir, exist_ok=True)

        iteration_log = {
            "iterations_used": score_result.get("iterations_used", 1),
            "feedback_applied": score_result.get("feedback_applied", []),
            "passed": score_result.get("passed", False),
        }

        output_path = generate_output(
            formatted_content=resume_content,
            jd_analysis=parsed_jd,
            score_report=score_report,
            keyword_report=keyword_report,
            reframing_log=reframing_log,
            format_validation=format_validation,
            iteration_log=iteration_log,
            pkb=pkb,
            output_dir=test_output_dir,
        )
        result["timings"]["generation"] = round(time.time() - t0, 1)
        result["output_path"] = output_path

        # Check output artifacts
        if os.path.exists(output_path):
            artifacts = os.listdir(output_path)
            result["generator"] = {
                "output_path": output_path,
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
                "has_pdf": any(f.endswith(".pdf") for f in artifacts),
                "has_docx": any(f.endswith(".docx") for f in artifacts),
            }
        else:
            result["generator"] = {"output_path": output_path, "error": "Output path not found"}
    except Exception as e:
        result["errors"].append(f"Generation failed: {str(e)}")
        logger.error("Test %d: Generation FAILED: %s", test_id, e)

    result["timings"]["total"] = round(time.time() - total_start, 1)

    # --- Fit Confidence ---
    total_score = result.get("scorer", {}).get("total_score", 0)
    if total_score >= 85:
        result["fit_confidence"] = "HIGH"
    elif total_score >= 75:
        result["fit_confidence"] = "MEDIUM"
    else:
        result["fit_confidence"] = "LOW"

    # Save individual test result JSON
    result_json_path = os.path.join(results_dir, f"test{test_id}_result.json")
    with open(result_json_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("Test %d result saved to %s", test_id, result_json_path)

    return result


def generate_comparison_report(all_results: list, results_dir: str) -> str:
    """Generate a markdown comparison report across all test results."""
    lines = []
    lines.append("# Placement Team — Test Suite Comparison Report")
    lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # --- Summary Table ---
    lines.append("## Summary Table\n")

    # Build header
    test_names = []
    for r in all_results:
        tid = r.get("test_id", "?")
        name = r.get("test_name", "Unknown")
        test_names.append(f"Test {tid}: {name}")

    header = "| Metric |"
    sep = "|---|"
    for tn in test_names:
        header += f" {tn} |"
        sep += "---|"
    lines.append(header)
    lines.append(sep)

    def _row(label, key_fn):
        row = f"| {label} |"
        for r in all_results:
            if r.get("error"):
                row += " ERROR |"
            else:
                val = key_fn(r)
                row += f" {val} |"
        return row

    lines.append(_row("P0 Keywords", lambda r: r.get("jd_parser", {}).get("p0_count", "-")))
    lines.append(_row("P1 Keywords", lambda r: r.get("jd_parser", {}).get("p1_count", "-")))
    lines.append(_row("P2 Keywords", lambda r: r.get("jd_parser", {}).get("p2_count", "-")))
    lines.append(_row("Total Keywords", lambda r: r.get("jd_parser", {}).get("total_keywords", "-")))
    lines.append(_row("Job Title", lambda r: r.get("jd_parser", {}).get("job_title", "-")[:30]))
    lines.append(_row("Direct Matches", lambda r: r.get("profile_mapper", {}).get("match_types", {}).get("DIRECT", "-")))
    lines.append(_row("Adjacent Matches", lambda r: r.get("profile_mapper", {}).get("match_types", {}).get("ADJACENT", "-")))
    lines.append(_row("Transferable", lambda r: r.get("profile_mapper", {}).get("match_types", {}).get("TRANSFERABLE", "-")))
    lines.append(_row("Gap Matches", lambda r: r.get("profile_mapper", {}).get("match_types", {}).get("GAP", "-")))
    lines.append(_row("P0 Coverage %", lambda r: r.get("profile_mapper", {}).get("p0_coverage_pct", "-")))
    lines.append(_row("**Final Score**", lambda r: f"**{r.get('scorer', {}).get('total_score', '-')}**"))
    lines.append(_row("Passed (>=90)", lambda r: "YES" if r.get("scorer", {}).get("passed") else "NO"))
    lines.append(_row("Iterations", lambda r: r.get("scorer", {}).get("iterations_used", "-")))
    lines.append(_row("Weakest Component", lambda r: r.get("scorer", {}).get("weakest_component", "-")))
    lines.append(_row("Fit Confidence", lambda r: r.get("fit_confidence", "-")))
    lines.append(_row("Format Warnings", lambda r: r.get("formatter", {}).get("warnings_count", "-")))
    lines.append(_row("Format Errors", lambda r: r.get("formatter", {}).get("errors_count", "-")))
    lines.append(_row("Est. Pages", lambda r: r.get("formatter", {}).get("estimated_pages", "-")))
    lines.append(_row("Time (seconds)", lambda r: r.get("timings", {}).get("total", "-")))
    lines.append(_row("PDF Generated", lambda r: "YES" if r.get("generator", {}).get("has_pdf") else "NO"))

    # --- Scoring Component Breakdown ---
    lines.append("\n## Score Component Breakdown\n")
    comp_header = "| Component |"
    comp_sep = "|---|"
    for tn in test_names:
        short = tn.split(":")[0].strip()
        comp_header += f" {short} |"
        comp_sep += "---|"
    lines.append(comp_header)
    lines.append(comp_sep)

    component_keys = [
        "keyword_match", "semantic_alignment", "parseability", "title_match",
        "impact", "brevity", "style", "narrative", "completeness", "anti_pattern",
    ]
    for ck in component_keys:
        row = f"| {ck} |"
        for r in all_results:
            comp = r.get("scorer", {}).get("components", {}).get(ck, {})
            score = comp.get("score", "-")
            row += f" {score} |"
        lines.append(row)

    # --- Flags / Red Alerts ---
    lines.append("\n## Flags & Alerts\n")
    flags = []
    for r in all_results:
        tid = r.get("test_id", "?")
        name = r.get("test_name", "Unknown")

        if r.get("error"):
            flags.append(f"- **RED FLAG** Test {tid} ({name}): Pipeline error — `{r['error']}`")
            continue

        total = r.get("scorer", {}).get("total_score", 0)
        if total < 80:
            flags.append(f"- **RED FLAG** Test {tid} ({name}): Score {total} < 80")
        elif total < 85:
            flags.append(f"- **WARNING** Test {tid} ({name}): Score {total} < 85")

        p0_count = r.get("jd_parser", {}).get("p0_count", 0)
        if p0_count > 20:
            flags.append(f"- **RED FLAG** Test {tid} ({name}): P0 count = {p0_count} > 20 — JD parser over-classifying")

        warn_count = r.get("formatter", {}).get("warnings_count", 0)
        if warn_count > 5:
            flags.append(f"- **WARNING** Test {tid} ({name}): {warn_count} format warnings")

        err_count = r.get("formatter", {}).get("errors_count", 0)
        if err_count > 0:
            flags.append(f"- **WARNING** Test {tid} ({name}): {err_count} format errors")

        total_time = r.get("timings", {}).get("total", 0)
        if total_time > 120:
            flags.append(f"- **WARNING** Test {tid} ({name}): Pipeline took {total_time}s > 120s")

        gap_count = r.get("profile_mapper", {}).get("match_types", {}).get("GAP", 0)
        total_mappings = r.get("profile_mapper", {}).get("total_mappings", 1)
        if total_mappings > 0 and gap_count / max(total_mappings, 1) > 0.5:
            flags.append(f"- **INFO** Test {tid} ({name}): >50% gap matches ({gap_count}/{total_mappings}) — expected for low-overlap roles")

    if flags:
        for flag in flags:
            lines.append(flag)
    else:
        lines.append("No flags raised. All tests within expected parameters.")

    # --- Fix Recommendations for Low-Scoring Tests ---
    low_scoring = [r for r in all_results if not r.get("error") and r.get("scorer", {}).get("total_score", 100) < 85]
    if low_scoring:
        lines.append("\n## Fix Recommendations (Tests Scoring < 85)\n")
        for r in low_scoring:
            tid = r.get("test_id", "?")
            name = r.get("test_name", "Unknown")
            total = r.get("scorer", {}).get("total_score", 0)
            weakest = r.get("scorer", {}).get("weakest_two") or []
            lines.append(f"### Test {tid}: {name} (Score: {total})")

            for w in weakest:
                comp_score = r.get("scorer", {}).get("components", {}).get(w, {}).get("score", 0)
                lines.append(f"- **{w}** (score: {comp_score})")

                if w == "keyword_match":
                    missing = r.get("keyword_optimizer", {}).get("missing_p0") or []
                    lines.append(f"  - Missing P0 keywords: {missing[:10]}")
                    lines.append("  - Fix: Improve reframer keyword injection or add terms to skills section")
                elif w == "title_match":
                    jd_title = r.get("jd_parser", {}).get("job_title", "")
                    lines.append(f"  - JD title: '{jd_title}'")
                    lines.append("  - Fix: Add title alias mapping in scorer (L5/L6 = Senior, etc)")
                elif w == "semantic_alignment":
                    lines.append("  - Fix: Reframer not aligning narrative with JD responsibilities")
                elif w == "impact":
                    lines.append("  - Fix: Add defensible metrics to unquantified bullets")
                elif w == "anti_pattern":
                    lines.append("  - Fix: Check for banned verbs, anachronistic tech, duplicate bullets")
                elif w == "brevity":
                    lines.append("  - Fix: Shorten bullets to 20-30 word range")
                else:
                    lines.append(f"  - Fix: Improve {w} score through targeted reframing")
            lines.append("")

    # --- Timing Breakdown ---
    lines.append("\n## Timing Breakdown (seconds)\n")
    time_header = "| Step |"
    time_sep = "|---|"
    for tn in test_names:
        short = tn.split(":")[0].strip()
        time_header += f" {short} |"
        time_sep += "---|"
    lines.append(time_header)
    lines.append(time_sep)

    for step in ["jd_parse", "profile_map", "reframe", "keyword_opt", "format", "scoring", "generation", "total"]:
        row = f"| {step} |"
        for r in all_results:
            val = r.get("timings", {}).get(step, "-")
            row += f" {val} |"
        lines.append(row)

    # --- Gaps Analysis ---
    lines.append("\n## Identified Gaps Across Tests\n")
    for r in all_results:
        if r.get("error"):
            continue
        tid = r.get("test_id", "?")
        name = r.get("test_name", "")
        gaps = r.get("profile_mapper", {}).get("gaps") or []
        if gaps:
            lines.append(f"**Test {tid} ({name}):** {', '.join(gaps[:10])}")

    # --- Overall Verdict ---
    lines.append("\n## Overall Verdict\n")
    passed = sum(1 for r in all_results if r.get("scorer", {}).get("passed"))
    total_tests = len(all_results)
    error_tests = sum(1 for r in all_results if r.get("error"))

    lines.append(f"- Tests run: {total_tests}")
    lines.append(f"- Tests passed (>=90): {passed}")
    lines.append(f"- Tests with errors: {error_tests}")
    lines.append(f"- Pass rate: {passed}/{total_tests - error_tests} ({100*passed/max(total_tests-error_tests,1):.0f}%)")

    if passed == total_tests - error_tests and error_tests == 0:
        lines.append("\n**VERDICT: ALL TESTS PASS. System is ready for real-world applications.**")
    elif passed >= 3:
        lines.append("\n**VERDICT: MOSTLY PASSING. Fix low-scoring tests before production use.**")
    else:
        lines.append("\n**VERDICT: NEEDS WORK. Review flags and fix recommendations above.**")

    report = "\n".join(lines)

    # Save report
    report_path = os.path.join(results_dir, "comparison_report.md")
    with open(report_path, "w") as f:
        f.write(report)
    logger.info("Comparison report saved to %s", report_path)

    return report_path


def main():
    parser = argparse.ArgumentParser(description="Placement Team — Test Suite Runner")
    parser.add_argument("--test", nargs="*", type=int, help="Test IDs to run (default: all)")
    parser.add_argument("--skip-generation", action="store_true", help="Skip PDF/DOCX generation (score-only mode)")
    args = parser.parse_args()

    jd_dir = os.path.join(PROJECT_ROOT, "tests", "sample_jds")
    results_dir = os.path.join(PROJECT_ROOT, "tests", "results")
    os.makedirs(results_dir, exist_ok=True)

    # Determine which tests to run
    test_ids = args.test if args.test else sorted(TEST_CASES.keys())

    # Validate test JD files exist
    missing = []
    for tid in test_ids:
        tc = TEST_CASES[tid]
        jd_path = os.path.join(jd_dir, tc["file"])
        if not os.path.exists(jd_path):
            missing.append(f"  Test {tid}: {jd_path}")

    if missing:
        logger.warning("Missing JD files:\n%s", "\n".join(missing))
        logger.warning("Skipping missing tests. Create the JD files and re-run.")
        test_ids = [tid for tid in test_ids if os.path.join(jd_dir, TEST_CASES[tid]["file"]) and os.path.exists(os.path.join(jd_dir, TEST_CASES[tid]["file"]))]

    if not test_ids:
        logger.error("No valid test JDs found. Create test JD files in %s", jd_dir)
        sys.exit(1)

    # Check PKB exists
    pkb_path = os.path.join(PROJECT_ROOT, "data", "pkb.json")
    if not os.path.exists(pkb_path):
        logger.error("PKB not found at %s. Run: python main.py --build-profile", pkb_path)
        sys.exit(1)

    # Run tests
    all_results = []
    logger.info("=" * 70)
    logger.info("PLACEMENT TEAM — TEST SUITE")
    logger.info("Running %d tests: %s", len(test_ids), test_ids)
    logger.info("=" * 70)

    for tid in test_ids:
        tc = TEST_CASES[tid]
        logger.info("")
        logger.info("-" * 60)
        logger.info("TEST %d: %s (%s)", tid, tc["name"], tc["type"])
        logger.info("Stress test: %s", tc["stress_test"])
        logger.info("-" * 60)

        try:
            result = run_single_test(tid, jd_dir, results_dir)
            all_results.append(result)

            if result.get("error"):
                logger.error("Test %d FAILED: %s", tid, result["error"])
            else:
                score = result.get("scorer", {}).get("total_score", 0)
                passed = result.get("scorer", {}).get("passed", False)
                elapsed = result.get("timings", {}).get("total", 0)
                confidence = result.get("fit_confidence", "?")
                status = "PASS" if passed else "FAIL"
                logger.info(
                    "Test %d COMPLETE: Score=%.1f [%s] | Confidence=%s | Time=%.1fs",
                    tid, score, status, confidence, elapsed,
                )
        except Exception as e:
            logger.error("Test %d CRASHED: %s", tid, e, exc_info=True)
            all_results.append({"test_id": tid, "error": f"Crash: {str(e)}"})

    # Generate comparison report
    logger.info("")
    logger.info("=" * 70)
    logger.info("GENERATING COMPARISON REPORT")
    logger.info("=" * 70)

    report_path = generate_comparison_report(all_results, results_dir)

    # Print summary to console
    print("\n" + "=" * 70)
    print("TEST SUITE RESULTS")
    print("=" * 70)
    for r in all_results:
        tid = r.get("test_id", "?")
        name = r.get("test_name", "Unknown")
        if r.get("error"):
            print(f"  Test {tid} ({name}): ERROR — {r['error']}")
        else:
            score = r.get("scorer", {}).get("total_score", 0)
            passed = "PASS" if r.get("scorer", {}).get("passed") else "FAIL"
            conf = r.get("fit_confidence", "?")
            iters = r.get("scorer", {}).get("iterations_used", 0)
            elapsed = r.get("timings", {}).get("total", 0)
            weakest = r.get("scorer", {}).get("weakest_component", "")
            print(f"  Test {tid} ({name:25s}): {score:5.1f} [{passed}] | {conf:6s} | {iters} iters | {elapsed:5.1f}s | weakest={weakest}")
    print(f"\nFull report: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
