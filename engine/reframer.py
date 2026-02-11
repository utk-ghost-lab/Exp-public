"""Step 3: Intelligent Reframing Engine (MOST CRITICAL FILE)

Generates tailored resume content following strict reframing rules:
- XYZ formula for every bullet
- Exact JD language matching
- Metrics on every bullet
- Semantic keyword clustering
- Interview-defensible reframing only

Input: Mapping matrix + PKB + JD analysis
Output: Tailored resume content with reframing log
"""

import json
import logging
import os
import re

import anthropic

# Load .env so ANTHROPIC_API_KEY is available when run from CLI/Composer
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Rule constants for programmatic enforcement
BANNED_START_VERBS = (
    "responsible for",
    "managed",
    "helped",
    "assisted",
    "participated",
    "planned",
)
REQUIRED_START_VERBS = (
    "led", "drove", "launched", "built", "owned", "delivered",
    "designed", "spearheaded", "achieved", "scaled", "transformed", "architected",
)
MAX_WORDS_PER_BULLET = 30
MAX_JD_KEYWORDS_PER_BULLET = 4
MAX_BULLETS_MOST_RECENT = 5
MAX_BULLETS_SECOND = 5
MAX_BULLETS_THIRD = 4
MAX_BULLETS_OLD_ROLE = 1
PRE_2023_CUTOFF_YEAR = 2023  # Roles ending before this: no "LLM-powered" / "GenAI"
WORDS_PER_PAGE_ESTIMATE = 400
MAX_PAGES = 2

REFRAME_PROMPT = """You are an expert resume reframing engine for ATS-optimized, interview-defensible resumes. Generate tailored resume content from the candidate's Profile Knowledge Base (PKB), using the JD analysis and mapping matrix. Follow ALL 13 rules below. These are final and non-negotiable.

RULE 1 — EXPERIENCE POSITIONING: Always present "8+ years of experience". Always position as "Senior Product Manager" in the summary. Frame as experienced senior leader.

RULE 2 — PROFESSIONAL SUMMARY: Maximum 3 lines. Must OPEN with "Senior Product Manager with 8+ years...". Include top 3 skills from the JD and 2-3 strongest metrics (e.g. 2.5× adoption, 75% engagement lift, 50% revenue growth). MUST reference the target company's domain: if beauty/wellness/salons/spas, reference service-based businesses or SMBs; if fintech, reference financial platforms. Make the hiring manager feel "this person gets our business." No filler. Summary should make a recruiter think "I need to call this person."

RULE 3 — BULLET STRUCTURE: XYZ: Accomplished [X] as measured by [Y], by doing [Z]. Every bullet MUST have a quantified metric. Maximum 20-30 words per bullet. Max 3-4 JD keywords per bullet. Lead each role with the most JD-relevant bullet. BANNED starts: "Responsible for", "Managed", "Helped", "Assisted", "Participated", "Planned". REQUIRED starts: Led, Drove, Launched, Built, Owned, Delivered, Designed, Spearheaded, Achieved, Scaled, Transformed, Architected. Only describe shipped/deployed work — do NOT use "planned" for features.

RULE 4 — BULLETS PER ROLE: Most recent: max 4-5. Second: max 5. Third: max 3-4. Older than 5 years: max 1-2 lines. Internships: 1 line max (omit if not relevant). Early career/developer: 1 line. If a bullet has no metric and you cannot estimate one, CUT it.

RULE 5 — ONLY RELEVANT POINTERS: Every bullet must map to P0 or P1 JD requirement. Does this help get shortlisted for THIS job? If no, cut it. 4 perfect bullets beat 8 mediocre ones.

RULE 6 — TOP 1% LANGUAGE: Outcomes, not tasks. Business impact: revenue, growth, retention, efficiency. Show WHY it mattered and the RESULT. Think: how would a VP describe this in a board presentation?

RULE 7 — REFRAMING BOUNDARIES: Allowed: change framing to match JD, use exact JD vocabulary, add defensible metrics, reorder bullets, elevate framing. Not allowed: invent work, claim tools never used. CRITICAL: For work before 2023, do NOT use "LLM-powered" or "GenAI". Use "conversational AI", "NLP-driven", or "ML-powered" for pre-2023 work. Do NOT use "planned" for features — only shipped/deployed work.

RULE 8 — KEYWORD USAGE: EXACT phrases from JD. P0: 2-3 times; P1: 1-2 times. No keyword more than 4 times. Distribute across summary + skills + experience.

RULE 9 — SKILLS SECTION: Max 25 terms total. Technical, Methodologies, Domains. Every term maps to P0 or P1. Add domain terms matching target company (e.g. POS, CRM for Zenoti). No filler.

RULE 10 — FORMAT: Date format "Mon YYYY – Mon YYYY". Single column. Standard headers.

RULE 11 — EDUCATION & CERTIFICATIONS: One line per education. Certifications relevant to JD only.

RULE 12 — TONE: Confident, human, crisp. No buzzword chains. How would Shreyas Doshi describe this?

RULE 13 — SELF-CHECK: Summary 3 lines, opens "Senior Product Manager with 8+ years", references target domain; no role >5 bullets; every bullet has metric, 20-30 words, no banned starts, ≤4 JD keywords; no pre-2023 "LLM-powered"; skills ≤25 terms; Fidelity 1 line; Cognizant 1 line; reframing_log complete.

REFRAMING LOG: For each reframed bullet: original, reframed, jd_keywords_used, what_changed, interview_prep.

Work experience: REVERSE-CHRONOLOGICAL. Same companies/titles from PKB. Apply bullet limits. Return ONLY valid JSON (no markdown)."""


def _format_dates_from_pkb(work: dict) -> str:
    """Format PKB dates to 'Jan 2020 – Mar 2023' style."""
    dates = work.get("dates") or {}
    start = dates.get("start") or ""
    end = dates.get("end") or ""
    if not start and not end:
        return ""
    if not end:
        return f"{start} – Present"
    return f"{start} – {end}"


def _extract_json_from_response(response_text: str) -> str:
    """Strip markdown code fences if present and return JSON string."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        json_lines = []
        in_json = False
        for line in lines:
            if line.strip().startswith("```") and not in_json:
                in_json = True
                continue
            if line.strip() == "```":
                break
            if in_json:
                json_lines.append(line)
        return "\n".join(json_lines)
    return text


def _word_count(text: str) -> int:
    return len(text.split())


def _shorten_bullet_to_max_words(bullet: str, max_words: int = MAX_WORDS_PER_BULLET) -> str:
    """Shorten bullet to at most max_words; prefer cut at sentence or clause boundary."""
    words = bullet.split()
    if len(words) <= max_words:
        return bullet
    truncated = " ".join(words[:max_words])
    # Prefer cutting at last comma or period before max_words to avoid "word and." or "75% engagement."
    for sep in (". ", ", ", "; ", "—", " "):
        last_sep = truncated.rfind(sep)
        if last_sep > len(truncated) * 0.5:
            truncated = truncated[: last_sep + len(sep)].rstrip()
            break
    if not truncated.rstrip().endswith((".", "!", "?")):
        truncated = truncated.rstrip(".,;") + "."
    return truncated


def _bullet_starts_with_banned(bullet: str) -> bool:
    first = bullet.strip().lower()
    for banned in BANNED_START_VERBS:
        if first.startswith(banned):
            return True
    return False


def _rewrite_banned_start(bullet: str) -> str:
    """Replace banned starting phrase with a required verb (Led/Drove/Owned)."""
    b = bullet.strip()
    lower = b.lower()
    for banned in BANNED_START_VERBS:
        if lower.startswith(banned):
            rest = b[len(banned):].lstrip(" :,-")
            if rest:
                return "Led " + rest[0].lower() + rest[1:] if len(rest) > 1 else "Led " + rest
            return "Led " + b
    return bullet


def _bullet_has_metric(bullet: str) -> bool:
    """True if bullet contains a number, %, $, or ×."""
    if not bullet:
        return False
    if "%" in bullet or "$" in bullet or "×" in bullet or "x" in bullet:
        return True
    if any(c.isdigit() for c in bullet):
        return True
    return False


def _count_jd_keywords_in_bullet(bullet: str, jd_keywords: list) -> int:
    lower = bullet.lower()
    count = 0
    for kw in jd_keywords:
        if kw and kw.lower() in lower:
            count += 1
    return count


def _get_role_end_year(role: dict, pkb: dict) -> int:
    """Extract end year from role dates. Default 2030 if unclear."""
    dates = role.get("dates")
    if isinstance(dates, dict):
        end = dates.get("end") or dates.get("start") or ""
    else:
        end = str(dates) if dates else ""
    if not end:
        company = role.get("company", "")
        for w in pkb.get("work_experience", []):
            if w.get("company") == company:
                d = w.get("dates") or {}
                end = d.get("end") or d.get("start") or ""
                break
    # Parse "Oct 2025" or "2025" or "May 2024 – Oct 2025"
    if isinstance(end, str) and end:
        match = re.search(r"20\d{2}|19\d{2}", end)
        if match:
            return int(match.group(0))
    return 2030


def _fix_pre_2023_language(bullet: str, role_end_year: int) -> str:
    """Replace LLM-powered/GenAI with conversational AI/ML-powered for pre-2023 roles."""
    if role_end_year >= PRE_2023_CUTOFF_YEAR:
        return bullet
    b = re.sub(r"\bLLM-powered\b", "conversational AI", bullet, flags=re.IGNORECASE)
    b = re.sub(r"\bGenAI\b", "ML-powered", b, flags=re.IGNORECASE)
    b = re.sub(r"\bLLM\b", "conversational AI", b)
    return b


def _enforce_bullet_limits(work_experience: list, pkb: dict) -> list:
    """Enforce max bullets per role: 5, 5, 4, then 1 for old/internship/developer."""
    pkb_order = [w["company"] for w in pkb.get("work_experience", [])]
    if not pkb_order:
        return work_experience
    result = []
    for i, role in enumerate(work_experience):
        company = (role.get("company") or "").strip()
        bullets = list(role.get("bullets") or [])
        if i == 0:
            cap = MAX_BULLETS_MOST_RECENT
        elif i == 1:
            cap = MAX_BULLETS_SECOND
        elif i == 2:
            cap = MAX_BULLETS_THIRD
        else:
            cap = MAX_BULLETS_OLD_ROLE
        if company.lower() in ("fidelity investments", "cognizant"):
            cap = 1
        if len(bullets) > cap:
            bullets = bullets[:cap]
        result.append({**role, "bullets": bullets})
    return result


def _estimate_page_count(result: dict) -> float:
    """Estimate number of pages from word count (~400 words/page)."""
    total = 0
    total += _word_count(result.get("professional_summary", ""))
    for role in result.get("work_experience", []):
        for b in role.get("bullets", []):
            total += _word_count(b)
    sk = result.get("skills", {})
    for v in (sk.get("technical") or []) + (sk.get("methodologies") or []) + (sk.get("domains") or []):
        total += _word_count(str(v))
    return total / WORDS_PER_PAGE_ESTIMATE


def _trim_to_fit_pages(result: dict, parsed_jd: dict, max_pages: float = MAX_PAGES) -> dict:
    """If over max_pages, drop weakest bullets (lowest JD keyword overlap) until fit."""
    pages = _estimate_page_count(result)
    if pages <= max_pages:
        return result
    p0_p1 = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])
    work = result.get("work_experience", [])
    all_bullets = []
    for ri, role in enumerate(work):
        for bi, bullet in enumerate(role.get("bullets", [])):
            score = _count_jd_keywords_in_bullet(bullet, p0_p1)
            all_bullets.append((ri, bi, bullet, score))
    all_bullets.sort(key=lambda x: x[3])
    drop = set()
    res = result
    while _estimate_page_count(res) > max_pages and all_bullets:
        ri, bi, _, _ = all_bullets.pop(0)
        drop.add((ri, bi))
        new_work = []
        for ri2, role in enumerate(res.get("work_experience", [])):
            bullets = [b for bi2, b in enumerate(role.get("bullets", [])) if (ri2, bi2) not in drop]
            new_work.append({**role, "bullets": bullets})
        res = {**res, "work_experience": new_work}
    return res


def _summary_references_domain(professional_summary: str, parsed_jd: dict) -> bool:
    """Check if summary references target company domain (e.g. service-based, beauty, fintech)."""
    ctx = (parsed_jd.get("company_context") or "").lower()
    company = (parsed_jd.get("company") or "").lower()
    summary = (professional_summary or "").lower()
    domain_terms = ["service-based", "smb", "salon", "spa", "beauty", "wellness", "fintech", "financial", "saas", "platform"]
    for term in domain_terms:
        if term in ctx or term in company:
            if term in summary or any(t in summary for t in ["service", "business", "platform", "retention", "conversion"]):
                return True
    return "service" in summary or "platform" in summary or "business" in summary


def run_rule13_self_check(result: dict, parsed_jd: dict, pkb: dict) -> dict:
    """Run Rule 13 self-check. Returns dict of check_name -> {passed: bool, message: str}."""
    checks = {}
    summary = result.get("professional_summary") or ""
    work = result.get("work_experience", [])
    p0_p1 = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])

    checks["summary_3_lines"] = {
        "passed": summary.count("\n") >= 2 or (len(summary) > 0 and "\n" in summary),
        "message": "Summary must be 3 lines",
    }
    if not summary.strip():
        checks["summary_3_lines"]["passed"] = False
    try:
        lines = [s.strip() for s in summary.split("\n") if s.strip()]
        checks["summary_3_lines"]["passed"] = len(lines) <= 3 and len(lines) >= 1
    except Exception:
        pass

    checks["summary_opens_8_years"] = {
        "passed": summary.strip().lower().startswith("senior product manager with 8+ years"),
        "message": "Summary must open with 'Senior Product Manager with 8+ years'",
    }

    checks["summary_references_domain"] = {
        "passed": _summary_references_domain(summary, parsed_jd),
        "message": "Summary must reference target company domain/industry",
    }

    no_role_over_5 = all(len(r.get("bullets") or []) <= 5 for r in work)
    checks["no_role_over_5_bullets"] = {"passed": no_role_over_5, "message": "No role has more than 5 bullets"}

    all_have_metric = True
    for r in work:
        for b in r.get("bullets", []):
            if not _bullet_has_metric(b):
                all_have_metric = False
                break
    checks["every_bullet_has_metric"] = {"passed": all_have_metric, "message": "Every bullet has a metric"}

    all_20_30_words = True
    for r in work:
        for b in r.get("bullets", []):
            wc = _word_count(b)
            if wc > MAX_WORDS_PER_BULLET or wc < 5:
                all_20_30_words = False
    checks["every_bullet_20_30_words"] = {"passed": all_20_30_words, "message": f"Every bullet 20-30 words (max {MAX_WORDS_PER_BULLET})"}

    no_banned_starts = True
    for r in work:
        for b in r.get("bullets", []):
            if _bullet_starts_with_banned(b):
                no_banned_starts = False
                break
    checks["no_banned_verb_starts"] = {"passed": no_banned_starts, "message": "No bullet starts with Managed, Responsible for, Helped, Planned"}

    no_over_4_keywords = True
    for r in work:
        for b in r.get("bullets", []):
            if _count_jd_keywords_in_bullet(b, p0_p1) > MAX_JD_KEYWORDS_PER_BULLET:
                no_over_4_keywords = False
                break
    checks["no_bullet_over_4_jd_keywords"] = {"passed": no_over_4_keywords, "message": f"No bullet has more than {MAX_JD_KEYWORDS_PER_BULLET} JD keywords"}

    no_pre_2023_llm = True
    for r in work:
        end_year = _get_role_end_year(r, pkb)
        if end_year < PRE_2023_CUTOFF_YEAR:
            for b in r.get("bullets", []):
                if "llm-powered" in b.lower() or "genai" in b.lower():
                    no_pre_2023_llm = False
                    break
    checks["no_pre_2023_llm_powered"] = {"passed": no_pre_2023_llm, "message": "No pre-2023 work claims LLM-powered"}

    pages = _estimate_page_count(result)
    checks["total_pages_1_5_2"] = {"passed": pages <= MAX_PAGES, "message": f"Total resume ≤{MAX_PAGES} pages (est. {pages:.1f})"}

    sk = result.get("skills", {})
    total_skills = len(sk.get("technical") or []) + len(sk.get("methodologies") or []) + len(sk.get("domains") or [])
    checks["skills_under_25"] = {"passed": total_skills <= 25, "message": f"Skills section ≤25 terms (has {total_skills})"}

    fidelity_1_line = True
    cognizant_1_line = True
    for r in work:
        c = (r.get("company") or "").lower()
        n = len(r.get("bullets") or [])
        if "fidelity" in c and n > 1:
            fidelity_1_line = False
        if "cognizant" in c and n > 1:
            cognizant_1_line = False
    checks["fidelity_1_line"] = {"passed": fidelity_1_line, "message": "Fidelity internship 1 line max"}
    checks["cognizant_1_line"] = {"passed": cognizant_1_line, "message": "Cognizant developer role 1 line max"}

    reframing_log = result.get("reframing_log") or []
    has_prep = all(e.get("interview_prep") for e in reframing_log)
    checks["reframing_log_complete"] = {"passed": len(reframing_log) >= 1 and has_prep, "message": "Reframing log complete with interview prep notes"}

    return checks


def _apply_programmatic_fixes(result: dict, parsed_jd: dict, pkb: dict) -> dict:
    """Apply all programmatic fixes: word count, banned verbs, metrics, pre-2023 language, bullet limits, page length."""
    work = result.get("work_experience", [])
    p0_p1 = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])
    new_work = []

    for role in work:
        end_year = _get_role_end_year(role, pkb)
        new_bullets = []
        for bullet in role.get("bullets", []):
            if not _bullet_has_metric(bullet):
                continue
            b = _fix_pre_2023_language(bullet, end_year)
            if _bullet_starts_with_banned(b):
                b = _rewrite_banned_start(b)
            wc = _word_count(b)
            if wc > MAX_WORDS_PER_BULLET:
                b = _shorten_bullet_to_max_words(b)
            kw_count = _count_jd_keywords_in_bullet(b, p0_p1)
            if kw_count > MAX_JD_KEYWORDS_PER_BULLET:
                b = _shorten_bullet_to_max_words(b, max_words=25)
            new_bullets.append(b)
        new_work.append({**role, "bullets": new_bullets})

    new_work = _enforce_bullet_limits(new_work, pkb)
    result = {**result, "work_experience": new_work}
    result = _trim_to_fit_pages(result, parsed_jd, max_pages=MAX_PAGES)
    # Cap skills at 25 terms total (prioritize technical, then methodologies, then domains)
    sk = result.get("skills", {})
    tech = sk.get("technical") or []
    meth = sk.get("methodologies") or []
    dom = sk.get("domains") or []
    total = tech + meth + dom
    if len(total) > 25:
        tech_cap = min(len(tech), 15)
        meth_cap = min(len(meth), 5)
        dom_cap = 25 - tech_cap - meth_cap
        result["skills"] = {
            "technical": tech[:tech_cap],
            "methodologies": meth[:meth_cap],
            "domains": dom[:max(0, dom_cap)],
        }
    return result


def reframe_experience(mapping_matrix: dict, pkb: dict, parsed_jd: dict) -> dict:
    """Generate tailored resume content using intelligent reframing.

    Args:
        mapping_matrix: JD-to-experience mappings from profile_mapper
        pkb: Profile Knowledge Base
        parsed_jd: Structured JD analysis from jd_parser

    Returns:
        Resume content dict with professional_summary, work_experience,
        skills, education, certifications, and reframing_log
    """
    client = anthropic.Anthropic()

    # Build context: JD + mapping + PKB
    jd_json = json.dumps({
        "job_title": parsed_jd.get("job_title"),
        "company": parsed_jd.get("company"),
        "location": parsed_jd.get("location"),
        "hard_skills": parsed_jd.get("hard_skills", []),
        "soft_skills": parsed_jd.get("soft_skills", []),
        "industry_terms": parsed_jd.get("industry_terms", []),
        "experience_requirements": parsed_jd.get("experience_requirements", []),
        "key_responsibilities": parsed_jd.get("key_responsibilities", []),
        "achievement_language": parsed_jd.get("achievement_language", []),
        "company_context": parsed_jd.get("company_context"),
        "job_level": parsed_jd.get("job_level"),
        "cultural_signals": parsed_jd.get("cultural_signals", []),
        "p0_keywords": parsed_jd.get("p0_keywords", []),
        "p1_keywords": parsed_jd.get("p1_keywords", []),
        "p2_keywords": parsed_jd.get("p2_keywords", []),
    }, indent=2)

    mapping_json = json.dumps(mapping_matrix, indent=2)
    pkb_json = json.dumps(pkb, indent=2)

    logger.info("Reframing experience with Claude (intelligent reframing engine)...")
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{REFRAME_PROMPT}\n\n"
                    "---\n\n"
                    "JOB DESCRIPTION ANALYSIS:\n"
                    f"{jd_json}\n\n"
                    "---\n\n"
                    "MAPPING MATRIX (JD requirements → candidate experience):\n"
                    f"{mapping_json}\n\n"
                    "---\n\n"
                    "CANDIDATE PROFILE KNOWLEDGE BASE (PKB):\n"
                    f"{pkb_json}"
                ),
            }
        ],
    )

    response_text = message.content[0].text.strip()
    json_str = _extract_json_from_response(response_text)

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse reframer output as JSON: %s", e)
        logger.error("Response preview: %s", response_text[:800])
        raise ValueError("LLM returned invalid JSON for reframing.") from e

    # Unwrap if LLM returned { "resume": { ... } }
    if "resume" in result and isinstance(result["resume"], dict):
        inner = result["resume"]
        result = {
            "professional_summary": inner.get("professional_summary", ""),
            "work_experience": inner.get("work_experience", []),
            "skills": inner.get("skills", {}),
            "education": inner.get("education", []),
            "certifications": inner.get("certifications", []),
            "reframing_log": result.get("reframing_log", inner.get("reframing_log", [])),
        }
        if "reframing_log" not in result or not result["reframing_log"]:
            result["reframing_log"] = inner.get("reframing_log", [])

    # Ensure required top-level keys exist
    result.setdefault("professional_summary", "")
    result.setdefault("work_experience", [])
    result.setdefault("skills", {"technical": [], "methodologies": [], "domains": []})
    result.setdefault("education", [])
    result.setdefault("certifications", [])
    result.setdefault("reframing_log", [])

    # Validate and normalize
    warnings = _validate_reframe_output(result, pkb)
    if warnings:
        for w in warnings:
            logger.warning("Reframe output: %s", w)
    else:
        logger.info("Reframe output validation passed")

    # Ensure work_experience dates match PKB order and format
    result["work_experience"] = _normalize_work_experience_dates(
        result.get("work_experience", []), pkb
    )

    # Programmatic enforcement: apply fixes (word count, banned verbs, metrics, pre-2023, bullet limits, page length)
    result = _apply_programmatic_fixes(result, parsed_jd, pkb)

    # Re-normalize dates after fixes (bullet order may have changed)
    result["work_experience"] = _normalize_work_experience_dates(
        result.get("work_experience", []), pkb
    )

    # Run Rule 13 self-check and attach results
    rule13 = run_rule13_self_check(result, parsed_jd, pkb)
    result["rule13_self_check"] = rule13
    all_passed = all(c.get("passed") for c in rule13.values())
    if not all_passed:
        logger.warning("Rule 13 self-check: some checks failed (see result['rule13_self_check'])")
    else:
        logger.info("Rule 13 self-check: all passed")

    logger.info(
        "  Summary length: %d chars; Work roles: %d; Reframing log entries: %d",
        len(result.get("professional_summary", "")),
        len(result.get("work_experience", [])),
        len(result.get("reframing_log", [])),
    )
    return result


def _normalize_work_experience_dates(work_experience: list, pkb: dict) -> list:
    """Ensure each role has correct dates from PKB and is in reverse-chronological order."""
    pkb_work = {w["company"]: w for w in pkb.get("work_experience", [])}
    for role in work_experience:
        company = role.get("company")
        if company and company in pkb_work:
            # Prefer PKB date format
            formatted = _format_dates_from_pkb(pkb_work[company])
            if formatted:
                role["dates"] = formatted
    # Sort by end date descending if we have PKB (most recent first)
    pkb_order = [w["company"] for w in pkb.get("work_experience", [])]
    if pkb_order:
        def sort_key(r):
            company = r.get("company", "")
            try:
                return -pkb_order.index(company)
            except ValueError:
                return 0
        work_experience.sort(key=sort_key)
    return work_experience


def _validate_reframe_output(result: dict, pkb: dict) -> list:
    """Validate reframer output structure. Returns list of warning strings."""
    warnings = []

    if not result.get("professional_summary"):
        warnings.append("professional_summary is empty")
    if not result.get("work_experience"):
        warnings.append("work_experience is empty")
    if not isinstance(result.get("skills"), dict):
        warnings.append("skills must be a dict with technical, methodologies, domains")
    else:
        sk = result["skills"]
        if not sk.get("technical") and not (sk.get("methodologies") or sk.get("domains")):
            warnings.append("skills should include technical and/or methodologies/domains")

    for i, role in enumerate(result.get("work_experience", [])):
        if not role.get("company"):
            warnings.append(f"work_experience[{i}] missing company")
        if not role.get("bullets"):
            warnings.append(f"work_experience[{i}] ({role.get('company')}) has no bullets")
        for j, bullet in enumerate(role.get("bullets", [])):
            if not bullet or not isinstance(bullet, str):
                warnings.append(f"work_experience[{i}].bullets[{j}] must be a non-empty string")

    reframing_log = result.get("reframing_log", [])
    for i, entry in enumerate(reframing_log):
        if not entry.get("original"):
            warnings.append(f"reframing_log[{i}] missing 'original'")
        if not entry.get("reframed"):
            warnings.append(f"reframing_log[{i}] missing 'reframed'")
        if not entry.get("interview_prep"):
            warnings.append(f"reframing_log[{i}] missing 'interview_prep'")

    if "education" not in result:
        warnings.append("missing 'education' (use [] if none)")
    if "certifications" not in result:
        warnings.append("missing 'certifications' (use [] if none)")

    return warnings


def main():
    """Run reframer on cached Zenoti JD + mapping + PKB. Saves reframed output for tests."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parsed_path = os.path.join(base, "tests", "sample_jds", "zenoti_pm_parsed.json")
    mapping_path = os.path.join(base, "tests", "sample_jds", "zenoti_pm_mapping.json")
    pkb_path = os.path.join(base, "data", "pkb.json")
    out_path = os.path.join(base, "tests", "sample_jds", "zenoti_pm_reframed.json")
    for p, name in [(parsed_path, "parsed JD"), (mapping_path, "mapping"), (pkb_path, "PKB")]:
        if not os.path.exists(p):
            print(f"Missing {name}: {p}", file=sys.stderr)
            sys.exit(1)
    with open(parsed_path) as f:
        parsed_jd = json.load(f)
    with open(mapping_path) as f:
        mapping = json.load(f)
    with open(pkb_path) as f:
        pkb = json.load(f)
    result = reframe_experience(mapping, pkb, parsed_jd)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Reframed output saved to {out_path}")


if __name__ == "__main__":
    main()
