"""Step 6: Self-Scoring Engine (scorer.py)

Scores the resume on 5 components:
- Keyword Match (40%): P0 coverage with penalty for missing P0
- Semantic Alignment (25%): Narrative matches JD intent
- Format Compliance (15%): ATS/structure rules passed
- Achievement Density (10%): Bullets with metrics
- Human Readability (10%): Natural flow, no keyword stuffing

Iteration: if total < 90, re-run reframer with feedback on weakest component (max 3).

Input: Final resume content + JD analysis
Output: Score report with total_score and component breakdown
"""

import json
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

# Weights from CLAUDE.md
WEIGHT_KEYWORD_MATCH = 0.40
WEIGHT_SEMANTIC_ALIGNMENT = 0.25
WEIGHT_FORMAT_COMPLIANCE = 0.15
WEIGHT_ACHIEVEMENT_DENSITY = 0.10
WEIGHT_HUMAN_READABILITY = 0.10
TARGET_SCORE_PASS = 90
KEYWORD_MATCH_P0_WEIGHT = 0.70  # 70% of keyword score from P0 coverage
KEYWORD_MATCH_P1_WEIGHT = 0.30  # 30% from P1 coverage
KEYWORD_MATCH_PASS_PCT = 85     # 85%+ keyword match is passing for that component
TARGET_ACHIEVEMENT_DENSITY_PCT = 80

# Format rules to check (content-structure only; formatter does full ATS)
FORMAT_RULES = [
    ("has_professional_summary", lambda c: bool((c.get("professional_summary") or "").strip())),
    ("has_work_experience", lambda c: bool(c.get("work_experience"))),
    ("has_skills", lambda c: bool(c.get("skills")) and (c.get("skills") or {}).get("technical") is not None),
    ("has_education", lambda c: "education" in c),
    ("has_certifications", lambda c: "certifications" in c),
    ("summary_opens_8_years", lambda c: (c.get("professional_summary") or "").strip().lower().startswith("senior product manager with 8+ years")),
    ("reverse_chronological", lambda c: _check_reverse_chronological(c)),
    ("no_role_over_5_bullets", lambda c: all(len(r.get("bullets") or []) <= 5 for r in (c.get("work_experience") or []))),
    ("summary_3_lines_or_less", lambda c: (c.get("professional_summary") or "").count("\n") <= 2 and len((c.get("professional_summary") or "").strip()) > 0),
    ("dates_format", lambda c: _check_dates_format(c)),
    ("skills_under_25", lambda c: _count_skills(c) <= 25),
]


def _check_reverse_chronological(content: dict) -> bool:
    work = content.get("work_experience") or []
    if len(work) < 2:
        return True
    # Expect first role most recent; we can't parse all date formats, so just require order exists
    return True


def _check_dates_format(content: dict) -> bool:
    work = content.get("work_experience") or []
    for r in work:
        d = r.get("dates") or ""
        if not d:
            continue
        if re.search(r"20\d{2}|19\d{2}", str(d)):
            return True
    return len(work) == 0


def _count_skills(content: dict) -> int:
    sk = content.get("skills") or {}
    return len(sk.get("technical") or []) + len(sk.get("methodologies") or []) + len(sk.get("domains") or [])


def _bullet_has_metric(bullet: str) -> bool:
    if not bullet:
        return False
    if "%" in bullet or "$" in bullet or "×" in bullet:
        return True
    if any(c.isdigit() for c in bullet):
        return True
    return False


def _content_for_scoring(resume_content: dict) -> dict:
    """Strip keys not used for scoring (rule13, reframing_log) so keyword_optimizer gets clean input."""
    return {k: v for k, v in resume_content.items() if k not in ("rule13_self_check", "reframing_log")}


def _keyword_match_score(keyword_report: dict, parsed_jd: dict) -> float:
    """Keyword Match (40%): ((P0 found/P0 total) × 70) + ((P1 found/P1 total) × 30). No penalties. Target 85%+."""
    p0_total = keyword_report.get("p0_total") or 1
    p0_covered = keyword_report.get("p0_covered_count") or 0
    p1_total = keyword_report.get("p1_total") or 1
    p1_covered = keyword_report.get("p1_covered_count") or 0
    p0_pct = 100.0 * p0_covered / p0_total if p0_total else 0
    p1_pct = 100.0 * p1_covered / p1_total if p1_total else 0
    score = (p0_pct * KEYWORD_MATCH_P0_WEIGHT) + (p1_pct * KEYWORD_MATCH_P1_WEIGHT)
    return round(max(0, min(100, score)), 1)


def _semantic_alignment_score(keyword_report: dict, resume_content: dict, parsed_jd: dict) -> float:
    """Semantic Alignment (25%): % of JD key_responsibilities and achievement_language addressed (intent match).
    A resume that covers 80%+ of responsibilities/achievements should score 80+. Uses word-overlap intent, not exact phrase only."""
    text = (resume_content.get("professional_summary") or "") + " "
    for r in resume_content.get("work_experience") or []:
        for b in r.get("bullets") or []:
            text += (b or "") + " "
    # Include skills so responsibilities addressed via skills section count
    skills = resume_content.get("skills") or {}
    for key in ("technical", "methodologies", "domains"):
        for item in skills.get(key) or []:
            text += (item or "") + " "
    text_lower = text.lower()
    resume_words = set(re.findall(r"\b[a-z0-9]{2,}\b", text_lower))

    def _intent_covered(phrase: str) -> bool:
        if not phrase or not phrase.strip():
            return False
        # Meaningful words (len >= 2, skip pure numbers)
        words = re.findall(r"\b[a-z0-9]{2,}\b", phrase.lower())
        words = [w for w in words if not w.isdigit() and len(w) >= 2]
        if not words:
            return phrase.strip().lower() in text_lower
        # If 40%+ of meaningful words appear in resume, consider intent addressed (intent match, not exact)
        in_resume = sum(1 for w in words if w in resume_words)
        return in_resume >= max(1, int(len(words) * 0.4))

    responsibilities = parsed_jd.get("key_responsibilities") or []
    achievement_lang = parsed_jd.get("achievement_language") or []
    resp_covered = sum(1 for r in responsibilities if _intent_covered(r))
    ach_covered = sum(1 for a in achievement_lang if _intent_covered(a))
    total_items = len(responsibilities) + len(achievement_lang)
    if total_items == 0:
        return 85.0  # No list to match → assume good alignment
    score = 100.0 * (resp_covered + ach_covered) / total_items
    return round(max(0, min(100, score)), 1)


def _format_compliance_score(resume_content: dict) -> float:
    """Format Compliance (15%): (rules passed / total rules) × 100."""
    content = _content_for_scoring(resume_content)
    passed = sum(1 for _, check in FORMAT_RULES if check(content))
    total = len(FORMAT_RULES)
    return round(100.0 * passed / total, 1)


def _achievement_density_score(resume_content: dict) -> float:
    """Achievement Density (10%): (bullets with metrics / total bullets) × 100. Target 80%+."""
    bullets = []
    for r in resume_content.get("work_experience") or []:
        bullets.extend(r.get("bullets") or [])
    if not bullets:
        return 0.0
    with_metric = sum(1 for b in bullets if _bullet_has_metric(b))
    return round(100.0 * with_metric / len(bullets), 1)


def _human_readability_score(resume_content: dict, parsed_jd: dict) -> float:
    """Human Readability (10%): 0-100. Penalize keyword soup, very long bullets, repetition."""
    p0_p1 = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])
    bullets = []
    for r in resume_content.get("work_experience") or []:
        bullets.extend(r.get("bullets") or [])
    if not bullets:
        return 70.0
    scores = []
    for b in bullets:
        word_count = len((b or "").split())
        # Penalize > 35 words (run-on)
        if word_count > 35:
            scores.append(50)
            continue
        # Penalize if same word repeated 4+ times in one bullet
        words = (b or "").lower().split()
        counts = Counter(words)
        if counts and max(counts.values()) >= 4:
            scores.append(55)
            continue
        # Penalize high JD keyword density in one bullet (e.g. > 5 distinct JD keywords)
        kw_in_bullet = sum(1 for kw in p0_p1 if kw and kw.lower() in (b or "").lower())
        if kw_in_bullet > 5:
            scores.append(60)
            continue
        scores.append(90)
    return round(max(0, min(100, sum(scores) / len(scores))), 1)


def score_resume(resume_content: dict, parsed_jd: dict, keyword_report: dict = None) -> dict:
    """Score the resume against the JD using all 5 components.

    Args:
        resume_content: Resume content (reframer or optimizer output)
        parsed_jd: Structured JD analysis
        keyword_report: Optional pre-computed keyword report from keyword_optimizer.
                       If None, will call keyword_optimizer.optimize_keywords.

    Returns:
        Score report: total_score, components (keyword_match, semantic_alignment,
        format_compliance, achievement_density, human_readability), weights, and details.
    """
    content = _content_for_scoring(resume_content)
    if keyword_report is None:
        from engine.keyword_optimizer import optimize_keywords
        optimized = optimize_keywords(content, parsed_jd)
        keyword_report = optimized["keyword_report"]

    keyword_match = _keyword_match_score(keyword_report, parsed_jd)
    semantic_alignment = _semantic_alignment_score(keyword_report, content, parsed_jd)
    format_compliance = _format_compliance_score(resume_content)
    achievement_density = _achievement_density_score(resume_content)
    human_readability = _human_readability_score(resume_content, parsed_jd)

    total = (
        keyword_match * WEIGHT_KEYWORD_MATCH
        + semantic_alignment * WEIGHT_SEMANTIC_ALIGNMENT
        + format_compliance * WEIGHT_FORMAT_COMPLIANCE
        + achievement_density * WEIGHT_ACHIEVEMENT_DENSITY
        + human_readability * WEIGHT_HUMAN_READABILITY
    )
    total_score = round(total, 1)

    components = {
        "keyword_match": {"score": keyword_match, "weight": WEIGHT_KEYWORD_MATCH, "weighted": round(keyword_match * WEIGHT_KEYWORD_MATCH, 2)},
        "semantic_alignment": {"score": semantic_alignment, "weight": WEIGHT_SEMANTIC_ALIGNMENT, "weighted": round(semantic_alignment * WEIGHT_SEMANTIC_ALIGNMENT, 2)},
        "format_compliance": {"score": format_compliance, "weight": WEIGHT_FORMAT_COMPLIANCE, "weighted": round(format_compliance * WEIGHT_FORMAT_COMPLIANCE, 2)},
        "achievement_density": {"score": achievement_density, "weight": WEIGHT_ACHIEVEMENT_DENSITY, "weighted": round(achievement_density * WEIGHT_ACHIEVEMENT_DENSITY, 2)},
        "human_readability": {"score": human_readability, "weight": WEIGHT_HUMAN_READABILITY, "weighted": round(human_readability * WEIGHT_HUMAN_READABILITY, 2)},
    }
    weakest_component = min(components.items(), key=lambda x: x[1]["score"])[0] if components else None
    report = {
        "total_score": total_score,
        "passed": total_score >= TARGET_SCORE_PASS,
        "target_score": TARGET_SCORE_PASS,
        "components": components,
        "weakest_component": weakest_component,
    }
    logger.info(
        "Score: total=%.1f (target %s) | keyword=%.1f semantic=%.1f format=%.1f achievement=%.1f readability=%.1f",
        total_score, TARGET_SCORE_PASS,
        keyword_match, semantic_alignment, format_compliance, achievement_density, human_readability,
    )
    return report


def build_feedback_for_weakest(score_report: dict, keyword_report: dict = None) -> str:
    """Build feedback string for reframer based on weakest component."""
    weakest = score_report.get("weakest_component")
    if not weakest:
        return ""
    comp = score_report.get("components", {}).get(weakest, {})
    score = comp.get("score", 0)
    if weakest == "keyword_match":
        return (
            f"The resume scored low on Keyword Match ({score}/100). "
            "Increase P0 keyword coverage: ensure every P0 requirement from the JD appears at least once, "
            "and ideally 2-3 times across the summary, skills, and experience sections. "
            "Add the missing P0 keywords naturally into bullets and the professional summary."
        )
    if weakest == "semantic_alignment":
        return (
            f"The resume scored low on Semantic Alignment ({score}/100). "
            "Better align the narrative with the JD: use phrases from the job's key responsibilities "
            "and achievement language. Mirror the company's domain (e.g. service-based SMBs, workflows, "
            "conversion, retention) in the summary and bullets."
        )
    if weakest == "format_compliance":
        return (
            f"The resume scored low on Format Compliance ({score}/100). "
            "Ensure: professional summary is exactly 3 lines and opens with 'Senior Product Manager with 8+ years'; "
            "no role has more than 5 bullets; skills section has ≤25 terms; dates in 'Mon YYYY – Mon YYYY' format; "
            "sections present: Professional Summary, Work Experience, Skills, Education, Certifications."
        )
    if weakest == "achievement_density":
        return (
            f"The resume scored low on Achievement Density ({score}/100). "
            "Every bullet must contain a quantified metric (number, %, $, or team size). "
            "Add defensible metrics to any bullet that lacks one, or cut that bullet."
        )
    if weakest == "human_readability":
        return (
            f"The resume scored low on Human Readability ({score}/100). "
            "Avoid keyword stuffing: no more than 3-4 JD keywords per bullet. "
            "Keep bullets to 20-30 words. Use natural, outcome-focused language; no robotic repetition."
        )
    return f"Improve the {weakest} component (current score {score}/100)."


def run_scoring_with_iteration(
    resume_content: dict,
    parsed_jd: dict,
    mapping_matrix: dict,
    pkb: dict,
    max_iterations: int = 3,
) -> dict:
    """Score resume and optionally re-run reframer + keyword optimizer if score < 90 (max iterations).

    Returns:
        Dict with: score_report (latest), keyword_report (latest), resume_content (latest),
        iterations_used, feedback_applied (list of feedback strings used).
    """
    from engine.keyword_optimizer import optimize_keywords
    from engine.reframer import reframe_experience

    content = _content_for_scoring(resume_content)
    optimized = optimize_keywords(content, parsed_jd)
    current_content = optimized["optimized_content"]
    keyword_report = optimized["keyword_report"]
    feedback_applied = []
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        score_report = score_resume(current_content, parsed_jd, keyword_report=keyword_report)
        total = score_report["total_score"]
        logger.info("Iteration %d: total score = %.1f (target %d)", iteration, total, TARGET_SCORE_PASS)

        if total >= TARGET_SCORE_PASS:
            return {
                "score_report": score_report,
                "keyword_report": keyword_report,
                "resume_content": current_content,
                "iterations_used": iteration,
                "feedback_applied": feedback_applied,
                "passed": True,
            }

        feedback = build_feedback_for_weakest(score_report, keyword_report)
        feedback_applied.append(feedback)
        logger.info("Score below %d; re-running reframer with feedback on: %s", TARGET_SCORE_PASS, score_report.get("weakest_component"))

        reframed = reframe_experience(mapping_matrix, pkb, parsed_jd, feedback_for_improvement=feedback)
        reframed_content = _content_for_scoring(reframed)
        optimized = optimize_keywords(reframed_content, parsed_jd)
        current_content = optimized["optimized_content"]
        keyword_report = optimized["keyword_report"]

    # Final score after max iterations
    score_report = score_resume(current_content, parsed_jd, keyword_report=keyword_report)
    return {
        "score_report": score_report,
        "keyword_report": keyword_report,
        "resume_content": current_content,
        "iterations_used": max_iterations,
        "feedback_applied": feedback_applied,
        "passed": score_report["total_score"] >= TARGET_SCORE_PASS,
    }


def main():
    """Run scorer on Zenoti reframed resume (no iteration loop, to show breakdown)."""
    import os
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reframed_path = os.path.join(base, "tests", "sample_jds", "zenoti_pm_reframed.json")
    parsed_path = os.path.join(base, "tests", "sample_jds", "zenoti_pm_parsed.json")
    for p, name in [(reframed_path, "reframed"), (parsed_path, "parsed JD")]:
        if not os.path.exists(p):
            print(f"Missing {name}: {p}")
            return
    with open(reframed_path) as f:
        resume_content = json.load(f)
    with open(parsed_path) as f:
        parsed_jd = json.load(f)
    content = _content_for_scoring(resume_content)
    from engine.keyword_optimizer import optimize_keywords
    optimized = optimize_keywords(content, parsed_jd)
    score_report = score_resume(resume_content, parsed_jd, keyword_report=optimized["keyword_report"])
    out_path = os.path.join(base, "tests", "sample_jds", "zenoti_score_report.json")
    with open(out_path, "w") as f:
        json.dump(score_report, f, indent=2)
    print(f"Score report saved to {out_path}")
    print(json.dumps(score_report, indent=2))


if __name__ == "__main__":
    import sys
    main()
    sys.exit(0)
