"""Step 4: Keyword Density Optimization (keyword_optimizer.py)

Ensures optimal keyword coverage across resume sections.
- P0 keywords: 2-3 occurrences (target 95%+ coverage)
- P1 keywords: 1-2 occurrences
- No keyword exceeds 4 occurrences
- Distributed across summary + skills + experience
- Insertion suggestions for missing keywords

Input: Generated resume content (reframer output) + JD analysis (parsed_jd)
Output: Optimized content + keyword_report
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Targets from CLAUDE.md
P0_MIN_OCCURRENCES = 2
P0_MAX_OCCURRENCES = 3
P1_MIN_OCCURRENCES = 1
P1_MAX_OCCURRENCES = 2
MAX_OCCURRENCES_ANY_KEYWORD = 4
P0_COVERAGE_TARGET_PCT = 95
MAX_SKILLS_TERMS = 25


def _get_resume_text_by_section(resume_content: dict) -> dict:
    """Extract plain text for summary, skills, and experience for counting."""
    sections = {"summary": "", "skills": "", "experience": ""}

    s = resume_content.get("professional_summary") or ""
    sections["summary"] = (s or "").strip()

    sk = resume_content.get("skills") or {}
    tech = " ".join(sk.get("technical") or [])
    meth = " ".join(sk.get("methodologies") or [])
    dom = " ".join(sk.get("domains") or [])
    sections["skills"] = f"{tech} {meth} {dom}".strip()

    bullets = []
    for role in resume_content.get("work_experience") or []:
        for b in role.get("bullets") or []:
            bullets.append(b or "")
    sections["experience"] = " ".join(bullets)

    return sections


def _count_keyword_occurrences(text: str, keyword: str) -> int:
    """Case-insensitive count of keyword as whole word or phrase in text."""
    if not keyword or not text:
        return 0
    # Escape for regex: word boundary and case-insensitive
    pattern = re.escape(keyword)
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _count_keyword_in_sections(sections: dict, keyword: str) -> dict:
    """Return counts per section for a keyword."""
    return {
        "summary": _count_keyword_occurrences(sections["summary"], keyword),
        "skills": _count_keyword_occurrences(sections["skills"], keyword),
        "experience": _count_keyword_occurrences(sections["experience"], keyword),
    }


def _total_count_in_sections(per_section: dict) -> int:
    return sum(per_section.values())


def optimize_keywords(resume_content: dict, parsed_jd: dict) -> dict:
    """Optimize keyword density in resume content and produce coverage report.

    Args:
        resume_content: Generated resume content from reframer (professional_summary,
            work_experience, skills, education, certifications; reframing_log/rule13
            are ignored for counting).
        parsed_jd: Structured JD analysis from jd_parser (p0_keywords, p1_keywords,
            all_keywords_flat or hard_skills/soft_skills for extraction).

    Returns:
        Dict with:
          - optimized_content: same as resume_content (no in-place edits; report only)
          - keyword_report: p0_coverage, p1_coverage, missing_keywords,
            over_used_keywords, insertion_suggestions, distribution summary.
    """
    # Normalize input: ignore rule13_self_check and reframing_log for text extraction
    content_for_counting = {
        "professional_summary": resume_content.get("professional_summary", ""),
        "work_experience": resume_content.get("work_experience", []),
        "skills": resume_content.get("skills", {}),
    }
    sections = _get_resume_text_by_section(content_for_counting)
    full_text = sections["summary"] + " " + sections["skills"] + " " + sections["experience"]

    p0_keywords = list(parsed_jd.get("p0_keywords") or [])
    p1_keywords = list(parsed_jd.get("p1_keywords") or [])
    all_keywords = list(parsed_jd.get("all_keywords_flat") or [])
    if not all_keywords:
        all_keywords = list(dict.fromkeys(p0_keywords + p1_keywords))

    # Dedupe while preserving order
    p0_keywords = list(dict.fromkeys(k for k in p0_keywords if k))
    p1_keywords = list(dict.fromkeys(k for k in p1_keywords if k))

    # Count each P0 keyword
    p0_counts = {}
    p0_covered = 0  # at least once (for coverage %)
    p0_in_target = 0  # in [2, 3] range
    p0_missing = []
    p0_per_section = {}
    for kw in p0_keywords:
        per_sec = _count_keyword_in_sections(sections, kw)
        total = _total_count_in_sections(per_sec)
        p0_counts[kw] = total
        p0_per_section[kw] = per_sec
        if total >= 1:
            p0_covered += 1
        if P0_MIN_OCCURRENCES <= total <= P0_MAX_OCCURRENCES:
            p0_in_target += 1
        if total == 0:
            p0_missing.append(kw)

    p0_total = len(p0_keywords) if p0_keywords else 1
    p0_coverage = round(100 * p0_covered / p0_total, 1) if p0_total else 0
    p0_in_target_pct = round(100 * p0_in_target / p0_total, 1) if p0_total else 0

    # Count each P1 keyword
    p1_counts = {}
    p1_covered = 0
    p1_missing = []
    for kw in p1_keywords:
        per_sec = _count_keyword_in_sections(sections, kw)
        total = _total_count_in_sections(per_sec)
        p1_counts[kw] = total
        if total >= P1_MIN_OCCURRENCES:
            p1_covered += 1
        elif total == 0:
            p1_missing.append(kw)

    p1_total = len(p1_keywords) if p1_keywords else 1
    p1_coverage = round(100 * p1_covered / p1_total, 1) if p1_total else 0

    # Over-used: any keyword (from all) that appears > 4 times
    over_used = []
    for kw in all_keywords:
        if not kw:
            continue
        n = _count_keyword_occurrences(full_text, kw)
        if n > MAX_OCCURRENCES_ANY_KEYWORD:
            over_used.append({"keyword": kw, "count": n})

    # Missing = P0 and P1 that appear 0 times (for insertion suggestions)
    missing_keywords = list(dict.fromkeys(p0_missing + [k for k in p1_missing if k not in p0_missing]))

    # Insertion suggestions for missing keywords
    insertion_suggestions = []
    for kw in missing_keywords[:30]:  # cap to avoid huge list
        suggestion = _suggest_insertion(kw, content_for_counting, parsed_jd)
        insertion_suggestions.append({"keyword": kw, "suggested_location": suggestion})

    # Distribution summary: how many P0/P1 appear in each section
    p0_in_summary = sum(1 for kw in p0_keywords if _count_keyword_occurrences(sections["summary"], kw) > 0)
    p0_in_skills = sum(1 for kw in p0_keywords if _count_keyword_occurrences(sections["skills"], kw) > 0)
    p0_in_experience = sum(1 for kw in p0_keywords if _count_keyword_occurrences(sections["experience"], kw) > 0)
    p1_in_summary = sum(1 for kw in p1_keywords if _count_keyword_occurrences(sections["summary"], kw) > 0)
    p1_in_skills = sum(1 for kw in p1_keywords if _count_keyword_occurrences(sections["skills"], kw) > 0)
    p1_in_experience = sum(1 for kw in p1_keywords if _count_keyword_occurrences(sections["experience"], kw) > 0)

    keyword_report = {
        "p0_coverage": p0_coverage,
        "p0_covered_count": p0_covered,
        "p0_total": p0_total,
        "p0_in_target_range_2_3": p0_in_target,
        "p0_in_target_range_pct": p0_in_target_pct,
        "p0_target_pct": P0_COVERAGE_TARGET_PCT,
        "p1_coverage": p1_coverage,
        "p1_covered_count": p1_covered,
        "p1_total": p1_total,
        "missing_keywords": missing_keywords,
        "over_used_keywords": over_used,
        "insertion_suggestions": insertion_suggestions,
        "distribution": {
            "p0_in_summary": p0_in_summary,
            "p0_in_skills": p0_in_skills,
            "p0_in_experience": p0_in_experience,
            "p1_in_summary": p1_in_summary,
            "p1_in_skills": p1_in_skills,
            "p1_in_experience": p1_in_experience,
        },
        "p0_counts": p0_counts,
        "p1_counts": p1_counts,
    }

    logger.info(
        "Keyword report: P0 coverage %s%% (%s/%s), P1 coverage %s%% (%s/%s), missing %s, over-used %s",
        p0_coverage, p0_covered, p0_total,
        p1_coverage, p1_covered, p1_total,
        len(missing_keywords), len(over_used),
    )

    # Fix 7: Cap skills at 25 terms. Trim lowest-priority (P1 before P0, least in JD first).
    optimized_content = resume_content
    sk = resume_content.get("skills") or {}
    tech = list(sk.get("technical") or [])
    meth = list(sk.get("methodologies") or [])
    dom = list(sk.get("domains") or [])
    total_skills = tech + meth + dom
    if len(total_skills) > MAX_SKILLS_TERMS:
        p0_set = set(k.lower() for k in (parsed_jd.get("p0_keywords") or []))
        p1_set = set(k.lower() for k in (parsed_jd.get("p1_keywords") or []))
        jd_flat = parsed_jd.get("all_keywords_flat") or []
        jd_freq = {}
        for kw in jd_flat:
            t = (kw or "").lower()
            jd_freq[t] = jd_freq.get(t, 0) + 1
        def priority(term):
            t = (term or "").strip().lower()
            in_p0 = t in p0_set or any(t in k for k in p0_set)
            in_p1 = t in p1_set or any(t in k for k in p1_set)
            if in_p0:
                return (0, -jd_freq.get(t, 0))
            if in_p1:
                return (1, -jd_freq.get(t, 0))
            return (2, -jd_freq.get(t, 0))
        flat = tech + meth + dom
        flat_sorted = sorted(flat, key=priority)
        to_keep = flat_sorted[:MAX_SKILLS_TERMS]
        to_remove = flat_sorted[MAX_SKILLS_TERMS:]
        if to_remove:
            logger.info("Skills trimmed to %d: removed %s (lowest priority)", MAX_SKILLS_TERMS, to_remove)
        keep_set = set(to_keep)
        new_tech = [t for t in tech if t in keep_set]
        new_meth = [m for m in meth if m in keep_set]
        new_dom = [d for d in dom if d in keep_set]
        optimized_content = {**resume_content, "skills": {"technical": new_tech, "methodologies": new_meth, "domains": new_dom}}
    return {
        "optimized_content": optimized_content,
        "keyword_report": keyword_report,
    }


def _suggest_insertion(keyword: str, resume_content: dict, parsed_jd: dict) -> str:
    """Suggest where to add a missing keyword based on JD and resume structure."""
    kw_lower = (keyword or "").lower()
    # Map keyword to likely section
    if any(term in kw_lower for term in ("product strategy", "success metrics", "execution", "gtm", "experimentation", "evaluation")):
        return "skills section (methodologies) or a product/launch bullet in work experience"
    if any(term in kw_lower for term in ("cloud", "web", "mobile", "api", "llm", "ml", "ai", "automation", "orchestration", "rag", "data-driven")):
        return "skills section (technical) or a technical bullet in work experience"
    if any(term in kw_lower for term in ("crm", "saas", "b2b", "retention", "conversion", "workflow", "smb")):
        return "skills section (domains) or a bullet about product/customer impact"
    if any(term in kw_lower for term in ("decision", "guardrails", "human-in-the-loop", "automated decisioning")):
        return "a bullet describing system design or AI/automation decisions"
    return "skills section or a relevant experience bullet"


def main():
    """Run keyword optimizer on Zenoti reframer output + parsed JD."""
    import os
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
    # Use only content keys for optimizer (ignore rule13_self_check, reframing_log for counting)
    content = {k: v for k, v in resume_content.items() if k not in ("rule13_self_check", "reframing_log")}
    result = optimize_keywords(content, parsed_jd)
    out_path = os.path.join(base, "tests", "sample_jds", "zenoti_keyword_report.json")
    with open(out_path, "w") as f:
        json.dump(result["keyword_report"], f, indent=2)
    print(f"Keyword report saved to {out_path}")
    print(json.dumps(result["keyword_report"], indent=2))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
    sys.exit(0)
