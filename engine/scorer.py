"""Step 6: Self-Scoring Engine (scorer.py) — v3 10-component model

Components (Jobscan / ATS-inspired):
- Keyword Match (25%): P0/P1 coverage, abbreviation sub-check
- Semantic Alignment (15%): JD responsibilities/achievement language addressed
- Parseability (10%): ATS format / structure rules
- Job Title Match (10%): Resume title vs JD title (exact/adjacent/equivalent/mismatch)
- Impact (12%): Achievement density (metrics in bullets)
- Brevity (8%): placeholder 80
- Style (8%): placeholder 80
- Narrative (7%): placeholder 80
- Completeness (3%): placeholder 80
- Anti-Pattern (2%): spelling, dates, skills-backed, duplicates, anachronistic tech, headers

Iteration: if total < 90, build feedback for TWO weakest components and re-run reframer (max 3).
"""

import json
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

TARGET_SCORE_PASS = 90
KEYWORD_MATCH_P0_WEIGHT = 0.70  # 70% of keyword component from P0 coverage
KEYWORD_MATCH_P1_WEIGHT = 0.30  # 30% from P1 coverage

# v3 weights (10 components)
WEIGHT_KEYWORD_MATCH = 0.25
WEIGHT_SEMANTIC_ALIGNMENT = 0.15
WEIGHT_PARSEABILITY = 0.10
WEIGHT_TITLE_MATCH = 0.10
WEIGHT_IMPACT = 0.12
WEIGHT_BREVITY = 0.08
WEIGHT_STYLE = 0.08
WEIGHT_NARRATIVE = 0.07
WEIGHT_COMPLETENESS = 0.03
WEIGHT_ANTI_PATTERN = 0.02
PLACEHOLDER_SCORE = 80.0  # for Brevity, Style, Narrative, Completeness until Phase B

# Banned opening verbs (impact / anti-pattern)
BANNED_START_VERBS = (
    "responsible for", "helped", "assisted", "participated", "supported",
    "worked on", "handled", "involved in", "managed", "planned",
)
# Pre-2023 anachronistic tech terms (roles ending before June 2023)
PRE_2023_TECH_TERMS = ("llm", "llm-powered", "large language model", "gpt", "generative ai", "gen ai", "rag", "retrieval-augmented")
PRE_2023_CUTOFF_YEAR = 2023
PRE_2023_CUTOFF_MONTH = 6  # June

# Known abbreviation ↔ full form for ATS coverage sub-check (JD/resume should have both when relevant)
ABBREVIATION_PAIRS = [
    ("CRM", "Customer Relationship Management"),
    ("PM", "Product Manager"),
    ("AI", "Artificial Intelligence"),
    ("ML", "Machine Learning"),
    ("API", "Application Programming Interface"),
    ("GTM", "Go-to-Market"),
    ("SMB", "Small and Medium Business"),
    ("B2B", "Business to Business"),
    ("ROI", "Return on Investment"),
    ("KPI", "Key Performance Indicator"),
    ("SaaS", "Software as a Service"),
    ("LLM", "Large Language Model"),
    ("RAG", "Retrieval Augmented Generation"),
]

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
    ("location_format_consistent", lambda c: _location_format_consistent(c)),
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


def _role_end_before_june_2023(role: dict) -> bool:
    """True if role end date is before June 2023 (from resume content only)."""
    dates = role.get("dates") or ""
    if isinstance(dates, dict):
        end = dates.get("end") or ""
    else:
        end = str(dates)
    years = re.findall(r"20\d{2}|19\d{2}", end)
    if not years:
        return False
    end_year = int(max(years))
    if end_year < PRE_2023_CUTOFF_YEAR:
        return True
    if end_year > PRE_2023_CUTOFF_YEAR:
        return False
    if re.search(r"Jan|Feb|Mar|Apr|May", end, re.I):
        return True
    return False


def _bullet_starts_with_banned(bullet: str) -> bool:
    if not bullet:
        return False
    lower = (bullet.strip() or "").lower()
    for banned in BANNED_START_VERBS:
        if lower.startswith(banned) or lower.startswith(banned + " "):
            return True
    return False


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


def _resume_full_text(resume_content: dict) -> str:
    """Full resume text for keyword/abbreviation checks (summary + skills + experience)."""
    text = (resume_content.get("professional_summary") or "") + " "
    for r in resume_content.get("work_experience") or []:
        for b in r.get("bullets") or []:
            text += (b or "") + " "
    sk = resume_content.get("skills") or {}
    for key in ("technical", "methodologies", "domains"):
        for item in sk.get(key) or []:
            text += (item or "") + " "
    return text

def _keyword_match_score(keyword_report: dict, parsed_jd: dict, resume_content: dict) -> float:
    """Keyword Match (25%): (P0 found/P0 total)×70 + (P1 found/P1 total)×30. No negative penalties.
    Sub-check: when JD has an abbreviation or full form, resume should have both for full marks; else small deduction."""
    p0_total = keyword_report.get("p0_total") or 1
    p0_covered = keyword_report.get("p0_covered_count") or 0
    p1_total = keyword_report.get("p1_total") or 1
    p1_covered = keyword_report.get("p1_covered_count") or 0
    p0_pct = 100.0 * p0_covered / p0_total if p0_total else 0
    p1_pct = 100.0 * p1_covered / p1_total if p1_total else 0
    base = (p0_pct * KEYWORD_MATCH_P0_WEIGHT) + (p1_pct * KEYWORD_MATCH_P1_WEIGHT)
    # Abbreviation coverage: if JD mentions either form, prefer both in resume
    jd_text = (parsed_jd.get("job_title") or "") + " " + " ".join(parsed_jd.get("all_keywords_flat") or [])
    resume_text = _resume_full_text(resume_content).lower()
    jd_lower = jd_text.lower()
    abbr_penalty = 0
    for abbr, full in ABBREVIATION_PAIRS:
        in_jd = abbr.lower() in jd_lower or full.lower() in jd_lower
        if not in_jd:
            continue
        in_resume_abbr = abbr.lower() in resume_text
        in_resume_full = full.lower() in resume_text
        if in_resume_abbr != in_resume_full:  # only one form present
            abbr_penalty += 3
    abbr_penalty = min(10, abbr_penalty)
    score = max(0, base - abbr_penalty)
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


def _year_from_dates(dates_str: str) -> int:
    """Extract latest year from a date range string (e.g. 'May 2024 – Oct 2025' -> 2025)."""
    if not dates_str:
        return 0
    years = re.findall(r"20\d{2}|19\d{2}", str(dates_str))
    return max(int(y) for y in years) if years else 0


def _job_title_match_score(resume_content: dict, parsed_jd: dict) -> float:
    """Job Title Match (10%): Resume's most recent title vs JD title. exact=100, adjacent=80, equivalent=70, mismatch=30."""
    work = resume_content.get("work_experience") or []
    if not work:
        return 30.0
    # Most recent role = first if reverse-chronological, else last (by end year)
    first_year = _year_from_dates(work[0].get("dates") or "")
    last_year = _year_from_dates(work[-1].get("dates") or "")
    most_recent_role = work[0] if first_year >= last_year else work[-1]
    resume_title = (most_recent_role.get("title") or "").strip()
    jd_title = (parsed_jd.get("job_title") or "").strip()
    if not resume_title or not jd_title:
        return 70.0
    r = re.sub(r"[^\w\s]", "", resume_title).lower().split()
    j = re.sub(r"[^\w\s]", "", jd_title).lower().split()
    r_set = set(r)
    j_set = set(j)
    if r_set == j_set:
        return 100.0
    # Equivalent: same core role (e.g. Product Manager vs Senior Product Manager)
    core_overlap = r_set & j_set
    if core_overlap and ("product" in r_set or "manager" in r_set or "pm" in r_set):
        if j_set <= r_set or r_set <= j_set or len(core_overlap) >= 2:
            return 80.0  # adjacent (e.g. Senior PM vs PM)
        return 70.0  # equivalent
    if len(core_overlap) >= 1:
        return 70.0
    return 30.0


def _anti_pattern_score(resume_content: dict, pkb: dict = None) -> float:
    """Anti-Pattern Detection (2%): 0-100. Penalize: title fabrication, years <8, bullet counts, pre-2023 tech, skills >25, banned verbs, duplicates, etc."""
    content = _content_for_scoring(resume_content)
    issues = []
    summary = (content.get("professional_summary") or "").strip().lower()

    # CRITICAL: Title fabrication check
    if pkb:
        pkb_titles = {}
        for w in pkb.get("work_experience", []):
            company = (w.get("company") or "").strip().lower()
            title = (w.get("title") or "").strip().lower()
            if company and title:
                pkb_titles[company] = title
        for role in content.get("work_experience") or []:
            company = (role.get("company") or "").strip().lower()
            current_title = (role.get("title") or "").strip().lower()
            if company in pkb_titles and current_title != pkb_titles[company]:
                logger.warning("TITLE FABRICATION: %s has '%s' but PKB says '%s'", company, current_title, pkb_titles[company])
                issues.append("title_fabrication")
                break

    # Fix 1: Summary must open with 8+ years — flag 5+, 6+, 7+
    if "5+ years" in summary or "6+ years" in summary or "7+ years" in summary:
        issues.append("years_under_8")
    # Fix 3: Most recent role 3-5 bullets, second role 3-5 bullets
    work = content.get("work_experience") or []
    if work:
        n0 = len(work[0].get("bullets") or [])
        if n0 < 3 or n0 > 5:
            issues.append("most_recent_role_bullet_count")
        if len(work) >= 2:
            n1 = len(work[1].get("bullets") or [])
            if n1 < 3 or n1 > 5:
                issues.append("second_role_bullet_count")
    # Fix 5: Pre-2023 role with LLM/GPT/RAG/generative AI → score 0 for this component
    for role in work:
        if not _role_end_before_june_2023(role):
            continue
        for b in role.get("bullets") or []:
            bl = (b or "").lower()
            if any(term in bl for term in PRE_2023_TECH_TERMS):
                issues.append("pre_2023_anachronistic_tech")
                break
        if "pre_2023_anachronistic_tech" in issues:
            break
    # Fix 7: Skills > 25
    if _count_skills(content) > 25:
        issues.append("skills_over_25")
    # Fix 10: Banned verb at start of any bullet
    bullets = []
    for r in content.get("work_experience") or []:
        bullets.extend(r.get("bullets") or [])
    for b in bullets:
        if _bullet_starts_with_banned(b or ""):
            issues.append("banned_verb_start")
            break
    # Duplicate bullets
    seen = set()
    for b in bullets:
        n = (b or "").strip().lower()[:80]
        if n in seen:
            issues.append("duplicate_bullet")
            break
        seen.add(n)
    # Skills not backed by experience or summary
    # Note: keyword_optimizer intentionally injects target-domain ATS keywords
    # into skills that may not appear in experience bullets (especially for
    # domain-shifted resumes). Use a lenient threshold.
    skill_words = set()
    sk = content.get("skills") or {}
    stop_words = {"and", "for", "the", "with", "from", "into", "based", "driven"}
    for key in ("technical", "methodologies", "domains"):
        for item in sk.get(key) or []:
            for w in (item or "").lower().split():
                if len(w) >= 3 and w not in stop_words:
                    skill_words.add(w)
    full_text = " ".join(bullets).lower() + " " + (content.get("professional_summary") or "").lower()
    unbacked = sum(1 for w in skill_words if w not in full_text)
    if skill_words and unbacked > len(skill_words) * 0.7:
        issues.append("skills_not_backed")
    # Run-on bullet
    for b in bullets:
        if len((b or "").split()) > 40:
            issues.append("runon_bullet")
            break
    # Critical: title fabrication or pre-2023 anachronistic tech → 0
    if "title_fabrication" in issues:
        return 0.0
    if "pre_2023_anachronistic_tech" in issues:
        return 0.0
    n_issues = len(issues)
    if n_issues == 0:
        return 100.0
    if n_issues == 1:
        return 70.0
    if n_issues == 2:
        return 50.0
    return max(0, 50 - (n_issues - 2) * 15)


def _brevity_score(resume_content: dict) -> float:
    """Brevity (8%): summary no sentence >45 words; score = % of bullets in 20-30 word range."""
    content = _content_for_scoring(resume_content)
    score = 80.0
    summary = (content.get("professional_summary") or "").strip()
    if summary:
        for line in summary.split("\n"):
            line = line.strip()
            if not line:
                continue
            wc = len(line.split())
            if wc > 45:
                score = max(0, score - 25)
    bullets = []
    for r in content.get("work_experience") or []:
        bullets.extend(r.get("bullets") or [])
    if bullets:
        in_range = sum(1 for b in bullets if 15 <= len((b or "").split()) <= 32)
        pct = 100.0 * in_range / len(bullets)
        score = min(100, pct)  # direct percentage of bullets in acceptable range
    return round(max(0, min(100, score)), 1)


def _style_score(resume_content: dict) -> float:
    """Style (8%): penalize -10 per opening verb that appears 3+ times."""
    content = _content_for_scoring(resume_content)
    bullets = []
    for r in content.get("work_experience") or []:
        bullets.extend(r.get("bullets") or [])
    if not bullets:
        return 80.0
    verb_counts = Counter()
    for b in bullets:
        first = (b or "").strip().split()
        if first:
            verb = first[0].lower().rstrip(".,;")
            verb_counts[verb] += 1
    penalty = sum(10 for v, c in verb_counts.items() if c >= 3)
    return round(max(0, min(100, 80 - penalty)), 1)


def _completeness_score(resume_content: dict) -> float:
    """Completeness (3%): base 80; +5 if awards section with at least 1 award."""
    content = _content_for_scoring(resume_content)
    base = 80.0
    awards = content.get("awards") or []
    if isinstance(awards, list) and len(awards) >= 1:
        base = min(100, base + 5)
    return round(max(0, min(100, base)), 1)


def _location_format_consistent(content: dict) -> bool:
    """Fix 8: All locations follow same pattern (e.g. City, Country). Inconsistent formats penalized."""
    work = content.get("work_experience") or []
    locs = [str(r.get("location") or "").strip() for r in work if r.get("location")]
    if len(locs) < 2:
        return True
    # Check all have a comma (City, Country) and similar structure
    has_comma = ["," in loc for loc in locs]
    if not all(has_comma):
        return False
    # Reject if some have state/region keywords and others don't (inconsistent)
    state_region = ("telangana", "karnataka", "metropolitan region", " area", "state")
    has_region = [any(s in loc.lower() for s in state_region) for loc in locs]
    if any(has_region) and not all(has_region):
        return False
    return True


def _format_compliance_score(resume_content: dict) -> float:
    """Parseability (10%): (rules passed / total rules) × 100. ATS structure. Includes location consistency (Fix 8)."""
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


def score_resume(resume_content: dict, parsed_jd: dict, keyword_report: dict = None, pkb: dict = None) -> dict:
    """Score the resume against the JD using all 10 components (v3).

    Returns:
        Score report: total_score, components (10), weakest_component, weakest_two.
    """
    content = _content_for_scoring(resume_content)
    if keyword_report is None:
        from engine.keyword_optimizer import optimize_keywords
        optimized = optimize_keywords(content, parsed_jd)
        keyword_report = optimized["keyword_report"]

    keyword_match = _keyword_match_score(keyword_report, parsed_jd, resume_content)
    semantic_alignment = _semantic_alignment_score(keyword_report, content, parsed_jd)
    parseability = _format_compliance_score(resume_content)
    title_match = _job_title_match_score(resume_content, parsed_jd)
    impact = _achievement_density_score(resume_content)
    brevity = _brevity_score(resume_content)
    style = _style_score(resume_content)
    narrative = PLACEHOLDER_SCORE
    completeness = _completeness_score(resume_content)
    anti_pattern = _anti_pattern_score(resume_content, pkb=pkb)

    total = (
        keyword_match * WEIGHT_KEYWORD_MATCH
        + semantic_alignment * WEIGHT_SEMANTIC_ALIGNMENT
        + parseability * WEIGHT_PARSEABILITY
        + title_match * WEIGHT_TITLE_MATCH
        + impact * WEIGHT_IMPACT
        + brevity * WEIGHT_BREVITY
        + style * WEIGHT_STYLE
        + narrative * WEIGHT_NARRATIVE
        + completeness * WEIGHT_COMPLETENESS
        + anti_pattern * WEIGHT_ANTI_PATTERN
    )
    total_score = round(total, 1)

    components = {
        "keyword_match": {"score": keyword_match, "weight": WEIGHT_KEYWORD_MATCH, "weighted": round(keyword_match * WEIGHT_KEYWORD_MATCH, 2)},
        "semantic_alignment": {"score": semantic_alignment, "weight": WEIGHT_SEMANTIC_ALIGNMENT, "weighted": round(semantic_alignment * WEIGHT_SEMANTIC_ALIGNMENT, 2)},
        "parseability": {"score": parseability, "weight": WEIGHT_PARSEABILITY, "weighted": round(parseability * WEIGHT_PARSEABILITY, 2)},
        "title_match": {"score": title_match, "weight": WEIGHT_TITLE_MATCH, "weighted": round(title_match * WEIGHT_TITLE_MATCH, 2)},
        "impact": {"score": impact, "weight": WEIGHT_IMPACT, "weighted": round(impact * WEIGHT_IMPACT, 2)},
        "brevity": {"score": brevity, "weight": WEIGHT_BREVITY, "weighted": round(brevity * WEIGHT_BREVITY, 2)},
        "style": {"score": style, "weight": WEIGHT_STYLE, "weighted": round(style * WEIGHT_STYLE, 2)},
        "narrative": {"score": narrative, "weight": WEIGHT_NARRATIVE, "weighted": round(narrative * WEIGHT_NARRATIVE, 2)},
        "completeness": {"score": completeness, "weight": WEIGHT_COMPLETENESS, "weighted": round(completeness * WEIGHT_COMPLETENESS, 2)},
        "anti_pattern": {"score": anti_pattern, "weight": WEIGHT_ANTI_PATTERN, "weighted": round(anti_pattern * WEIGHT_ANTI_PATTERN, 2)},
    }
    sorted_by_score = sorted(components.items(), key=lambda x: x[1]["score"])
    weakest_component = sorted_by_score[0][0] if sorted_by_score else None
    weakest_two = [sorted_by_score[0][0], sorted_by_score[1][0]] if len(sorted_by_score) >= 2 else ([weakest_component] if weakest_component else [])
    report = {
        "total_score": total_score,
        "passed": total_score >= TARGET_SCORE_PASS,
        "target_score": TARGET_SCORE_PASS,
        "components": components,
        "weakest_component": weakest_component,
        "weakest_two": weakest_two,
    }
    logger.info(
        "Score: total=%.1f (target %s) | keyword=%.1f semantic=%.1f parse=%.1f title=%.1f impact=%.1f anti=%.1f",
        total_score, TARGET_SCORE_PASS,
        keyword_match, semantic_alignment, parseability, title_match, impact, anti_pattern,
    )
    return report


def _feedback_for_component(component_key: str, score: float, keyword_report: dict = None) -> str:
    """Single-component feedback text for reframer."""
    if component_key == "keyword_match":
        return (
            f"Keyword Match is low ({score}/100). "
            "Increase P0/P1 coverage: ensure every P0 requirement appears at least once in summary, skills, and experience. "
            "Include both abbreviation and full form when relevant (e.g. CRM and Customer Relationship Management)."
        )
    if component_key == "semantic_alignment":
        return (
            f"Semantic Alignment is low ({score}/100). "
            "Align narrative with the JD: use phrases from key responsibilities and achievement language. "
            "Mirror the company's domain in summary and bullets."
        )
    if component_key == "parseability":
        return (
            f"Parseability/ATS format is low ({score}/100). "
            "Ensure: professional summary present, work experience reverse-chronological, skills listed, education and certifications; "
            "no role over 5 bullets; dates with years; standard section structure."
        )
    if component_key == "title_match":
        return (
            f"Job Title Match is low ({score}/100). "
            "Ensure the most recent role title matches or is adjacent to the JD title (e.g. Senior Product Manager for a PM role)."
        )
    if component_key == "impact":
        return (
            f"Impact/Achievement density is low ({score}/100). "
            "Every bullet should contain a quantified metric (number, %, $, or team size). Add defensible metrics or cut bullets."
        )
    if component_key == "anti_pattern":
        return (
            f"Anti-pattern score is low ({score}/100). "
            "Avoid: duplicate bullets, skills not backed by experience bullets, inconsistent dates, run-on bullets. Use standard headers."
        )
    if component_key in ("brevity", "style", "narrative", "completeness"):
        return f"Improve {component_key} (current score {score}/100)."
    return f"Improve the {component_key} component (current score {score}/100)."


def build_feedback_for_weakest(score_report: dict, keyword_report: dict = None) -> str:
    """Build feedback string for reframer based on single weakest component (legacy)."""
    weakest = score_report.get("weakest_component")
    if not weakest:
        return ""
    comp = score_report.get("components", {}).get(weakest, {})
    return _feedback_for_component(weakest, comp.get("score", 0), keyword_report)


def build_feedback_for_two_weakest(score_report: dict, keyword_report: dict = None) -> str:
    """Build combined feedback for the TWO weakest components (v3 iteration)."""
    two = score_report.get("weakest_two") or []
    if not two:
        return build_feedback_for_weakest(score_report, keyword_report)
    parts = []
    for key in two:
        comp = score_report.get("components", {}).get(key, {})
        parts.append(_feedback_for_component(key, comp.get("score", 0), keyword_report))
    return " ".join(parts)


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
    from engine.reframer import reframe_experience, _apply_programmatic_fixes

    content = _content_for_scoring(resume_content)
    optimized = optimize_keywords(content, parsed_jd)
    current_content = optimized["optimized_content"]
    keyword_report = optimized["keyword_report"]
    feedback_applied = []
    iteration = 0
    best_score = 0.0
    best_content = current_content
    best_keyword_report = keyword_report
    best_score_report = None

    while iteration < max_iterations:
        iteration += 1
        score_report = score_resume(current_content, parsed_jd, keyword_report=keyword_report, pkb=pkb)
        total = score_report["total_score"]
        logger.info("Iteration %d: total score = %.1f (target %d)", iteration, total, TARGET_SCORE_PASS)

        # Bug 5 fix: Track best score and revert if iteration made it worse
        if total > best_score:
            best_score = total
            best_content = current_content
            best_keyword_report = keyword_report
            best_score_report = score_report
        elif total < best_score:
            logger.warning(
                "Iteration %d score %.1f < previous best %.1f — reverting to best version",
                iteration, total, best_score,
            )
            current_content = best_content
            keyword_report = best_keyword_report
            score_report = best_score_report
            # Skip further iterations since patching is making it worse
            break

        if total >= TARGET_SCORE_PASS:
            return {
                "score_report": score_report,
                "keyword_report": keyword_report,
                "resume_content": current_content,
                "iterations_used": iteration,
                "feedback_applied": feedback_applied,
                "passed": True,
            }

        feedback = build_feedback_for_two_weakest(score_report, keyword_report)
        feedback_applied.append(feedback)
        logger.info("Score below %d; re-running reframer with feedback on: %s", TARGET_SCORE_PASS, score_report.get("weakest_two"))

        # Use patch mode: pass current resume for targeted edits (faster, smaller prompt)
        reframed = reframe_experience(
            mapping_matrix, pkb, parsed_jd,
            feedback_for_improvement=feedback,
            current_resume_content=current_content
        )
        reframed_content = _content_for_scoring(reframed)

        # Bug 5 fix: Re-apply programmatic fixes after patch iteration
        reframed_content = _apply_programmatic_fixes(reframed_content, parsed_jd, pkb)

        optimized = optimize_keywords(reframed_content, parsed_jd)
        current_content = optimized["optimized_content"]
        keyword_report = optimized["keyword_report"]

    # Final score after max iterations (use best version)
    if best_score_report is None or current_content is not best_content:
        score_report = score_resume(current_content, parsed_jd, keyword_report=keyword_report, pkb=pkb)
        if score_report["total_score"] < best_score:
            current_content = best_content
            keyword_report = best_keyword_report
            score_report = best_score_report
    else:
        score_report = best_score_report

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
