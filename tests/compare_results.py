"""Fast Validation Comparison Script — Step 9

Reads score_report.json, keyword_coverage.json, format_warnings.json from both
test output folders and generates a side-by-side comparison report.

Usage:
    python tests/compare_results.py [output_dir1] [output_dir2]

If no args, auto-discovers the two most recent output folders.
"""

import json
import os
import sys
from datetime import datetime
from glob import glob


def find_output_folders(base="output"):
    """Find output folders sorted by modification time (most recent first)."""
    if not os.path.isdir(base):
        return []
    folders = [
        os.path.join(base, d)
        for d in os.listdir(base)
        if os.path.isdir(os.path.join(base, d))
    ]
    folders.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return folders


def load_json(path):
    """Load JSON file, return empty dict if missing."""
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def extract_resume_content(folder):
    """Load the reframing_log.json or iteration_log.json to get resume content,
    or read score_report.json for component data."""
    # Try to find the resume content from keyword_coverage or reframing_log
    score = load_json(os.path.join(folder, "score_report.json"))
    keywords = load_json(os.path.join(folder, "keyword_coverage.json"))
    reframing = load_json(os.path.join(folder, "reframing_log.json"))
    iteration = load_json(os.path.join(folder, "iteration_log.json"))
    warnings = load_json(os.path.join(folder, "format_warnings.json"))
    interview = ""
    interview_path = os.path.join(folder, "interview_prep.md")
    if os.path.isfile(interview_path):
        with open(interview_path) as f:
            interview = f.read()
    return {
        "score": score,
        "keywords": keywords,
        "reframing": reframing,
        "iteration": iteration,
        "warnings": warnings,
        "interview": interview,
        "folder": folder,
    }


def get_folder_label(folder):
    """Extract company name from folder path."""
    basename = os.path.basename(folder)
    # Format: CompanyName_2026-02-13
    parts = basename.rsplit("_", 1)
    return parts[0] if parts else basename


def count_format_issues(warnings_data):
    """Count errors and warnings from format_warnings.json."""
    if isinstance(warnings_data, dict):
        return warnings_data.get("errors_count", 0), warnings_data.get("warnings_count", 0)
    if isinstance(warnings_data, list):
        errors = sum(1 for w in warnings_data if w.get("severity") == "ERROR")
        warns = sum(1 for w in warnings_data if w.get("severity") == "WARN")
        return errors, warns
    return 0, 0


def estimate_pages(score_data):
    """Estimate pages from score data or keyword data."""
    # Check if score has page info
    return score_data.get("estimated_pages", "N/A")


def build_report(data1, data2, label1, label2):
    """Build the markdown comparison report."""
    s1 = data1["score"]
    s2 = data2["score"]
    k1 = data1["keywords"]
    k2 = data2["keywords"]
    w1 = data1["warnings"]
    w2 = data2["warnings"]
    it1 = data1["iteration"]
    it2 = data2["iteration"]

    c1 = s1.get("components", {})
    c2 = s2.get("components", {})

    def comp_score(components, key):
        c = components.get(key, {})
        if isinstance(c, dict):
            return c.get("score", "N/A")
        return c

    # P0/P1/P2 from keyword report
    p0_1 = k1.get("p0_total", "N/A")
    p1_1 = k1.get("p1_total", "N/A")
    p0_2 = k2.get("p0_total", "N/A")
    p1_2 = k2.get("p1_total", "N/A")

    # Match types from keyword report
    direct1 = k1.get("p0_covered_count", "N/A")
    direct2 = k2.get("p0_covered_count", "N/A")

    e1, wa1 = count_format_issues(w1)
    e2, wa2 = count_format_issues(w2)

    total1 = s1.get("total_score", "N/A")
    total2 = s2.get("total_score", "N/A")

    iters1 = it1.get("iterations_used", "N/A")
    iters2 = it2.get("iterations_used", "N/A")

    # Fit confidence
    def fit_conf(score):
        if not isinstance(score, (int, float)):
            return "UNKNOWN"
        if score >= 85:
            return "HIGH"
        if score >= 75:
            return "MEDIUM"
        return "LOW"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""# Fast Validation Report — Step 9
Date: {now}

## Side-by-Side Comparison

| Metric                    | Test 1: {label1} | Test 2: {label2} |
|---------------------------|{'─' * 24}|{'─' * 25}|
| Company                   | {label1} | {label2} |
| Expected difficulty       | STRETCH                | SOLID MATCH             |
|                           |                        |                         |
| **Scoring (10 components)**|                       |                         |
| keyword_match (x0.25)     | {comp_score(c1, 'keyword_match')} | {comp_score(c2, 'keyword_match')} |
| semantic_alignment (x0.15)| {comp_score(c1, 'semantic_alignment')} | {comp_score(c2, 'semantic_alignment')} |
| parseability (x0.10)      | {comp_score(c1, 'parseability')} | {comp_score(c2, 'parseability')} |
| title_match (x0.10)       | {comp_score(c1, 'title_match')} | {comp_score(c2, 'title_match')} |
| impact (x0.12)            | {comp_score(c1, 'impact')} | {comp_score(c2, 'impact')} |
| brevity (x0.08)           | {comp_score(c1, 'brevity')} | {comp_score(c2, 'brevity')} |
| style (x0.08)             | {comp_score(c1, 'style')} | {comp_score(c2, 'style')} |
| narrative (x0.07)         | {comp_score(c1, 'narrative')} | {comp_score(c2, 'narrative')} |
| completeness (x0.03)      | {comp_score(c1, 'completeness')} | {comp_score(c2, 'completeness')} |
| anti_pattern (x0.02)      | {comp_score(c1, 'anti_pattern')} | {comp_score(c2, 'anti_pattern')} |
| **TOTAL**                 | **{total1}** | **{total2}** |
|                           |                        |                         |
| **Keyword Coverage**      |                        |                         |
| P0 coverage               | {k1.get('p0_coverage', 'N/A')}% ({k1.get('p0_covered_count', '?')}/{p0_1}) | {k2.get('p0_coverage', 'N/A')}% ({k2.get('p0_covered_count', '?')}/{p0_2}) |
| P1 coverage               | {k1.get('p1_coverage', 'N/A')}% ({k1.get('p1_covered_count', '?')}/{p1_1}) | {k2.get('p1_coverage', 'N/A')}% ({k2.get('p1_covered_count', '?')}/{p1_2}) |
| Missing keywords           | {len(k1.get('missing_keywords', []))} | {len(k2.get('missing_keywords', []))} |
| Over-used keywords          | {len(k1.get('over_used_keywords', []))} | {len(k2.get('over_used_keywords', []))} |
|                           |                        |                         |
| **Output Quality**        |                        |                         |
| Iterations needed          | {iters1} | {iters2} |
| Format warnings            | {wa1} | {wa2} |
| Format errors              | {e1} | {e2} |
| Fit confidence             | {fit_conf(total1)} | {fit_conf(total2)} |

## What We Expect

**Test 1 ({label1}) — STRETCH:**
- Role requires 10+ years and 3+ years people management
- Deep FP&A domain — Planful experience is relevant but may not be deep FP&A
- GPM/Principal level is a step up from Senior PM
- Expected score: 72-82. If higher, check whether reframer over-stretched.

**Test 2 ({label2}) — SOLID MATCH:**
- 5-8+ years PM experience (candidate has 8+)
- AI/ML concepts (Planful AI roadmap, Alexa bot, LLM prototyping)
- Product Led Growth (Planful web adoption 2.5x, Wealthy engagement 75%)
- Expected score: 85-92. If lower, something is broken.

## Gate Checks

### GATE 1: Did both run without crashing?
- [{"x" if total1 != "N/A" else " "}] Test 1 completed successfully
- [{"x" if total2 != "N/A" else " "}] Test 2 completed successfully

### GATE 2: Are the scores in the right ballpark?
- [{"x" if isinstance(total1, (int, float)) and 70 <= total1 <= 85 else " "}] Test 1 (Intuit STRETCH): Score {total1} — target 70-85
- [{"x" if isinstance(total2, (int, float)) and 85 <= total2 <= 95 else " "}] Test 2 (Microsoft MATCH): Score {total2} — target 85-95
- [{"x" if isinstance(total1, (int, float)) and isinstance(total2, (int, float)) and total2 > total1 else " "}] Test 2 scores HIGHER than Test 1

### GATE 5: Anti-pattern check
- Weakest component Test 1: {s1.get('weakest_component', 'N/A')}
- Weakest component Test 2: {s2.get('weakest_component', 'N/A')}

## Missing P0 Keywords

**Test 1:** {k1.get('missing_keywords', [])[:10]}
**Test 2:** {k2.get('missing_keywords', [])[:10]}

## VERDICT

{"ALL GATES PASS" if (isinstance(total1, (int, float)) and isinstance(total2, (int, float)) and total2 > total1 and total1 >= 70 and total2 >= 85) else "REVIEW NEEDED — see gate checks above"}
"""
    return report


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_base = os.path.join(base, "output")

    if len(sys.argv) >= 3:
        folder1, folder2 = sys.argv[1], sys.argv[2]
    else:
        folders = find_output_folders(output_base)
        if len(folders) < 2:
            print(f"Need at least 2 output folders in {output_base}. Found: {len(folders)}")
            sys.exit(1)
        folder1, folder2 = folders[1], folders[0]  # second-newest, newest

    label1 = get_folder_label(folder1)
    label2 = get_folder_label(folder2)

    print(f"Comparing:\n  Test 1: {folder1} ({label1})\n  Test 2: {folder2} ({label2})\n")

    data1 = extract_resume_content(folder1)
    data2 = extract_resume_content(folder2)

    report = build_report(data1, data2, label1, label2)

    results_dir = os.path.join(base, "tests", "results")
    os.makedirs(results_dir, exist_ok=True)
    report_path = os.path.join(results_dir, "fast_validation_report.md")
    with open(report_path, "w") as f:
        f.write(report)

    print(report)
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
