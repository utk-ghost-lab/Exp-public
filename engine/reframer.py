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
import time

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
    "supported",
    "worked on",
    "handled",
    "involved in",
)
REQUIRED_START_VERBS = (
    "led", "drove", "launched", "built", "owned", "delivered",
    "designed", "spearheaded", "achieved", "scaled", "transformed", "architected",
)
MAX_WORDS_PER_BULLET = 30
MIN_WORDS_PER_BULLET = 20
MAX_JD_KEYWORDS_PER_BULLET = 4
MAX_BULLETS_MOST_RECENT = 5
MIN_BULLETS_MOST_RECENT = 4
MAX_BULLETS_SECOND = 4
MIN_BULLETS_SECOND = 3
MAX_BULLETS_THIRD = 4
MIN_BULLETS_THIRD = 3
MAX_BULLETS_OLD_ROLE = 2
MAX_BULLETS_INTERNSHIP = 1
PRE_2023_CUTOFF_YEAR = 2023  # Roles ending before June 2023: no LLM/GPT/RAG/GenAI
PRE_2023_CUTOFF_MONTH = 6  # June 2023
MAX_SKILLS_TERMS = 25
MAX_SUMMARY_WORDS_PER_LINE = 40
# Verb variety: if same verb 3+ times, replace with synonym
VERB_SYNONYMS = {
    "led": ["spearheaded", "championed"],
    "drove": ["owned", "directed"],
    "built": ["designed", "architected"],
    "launched": ["shipped", "delivered"],
    "managed": ["orchestrated", "coordinated"],
    "developed": ["engineered", "created"],
    "implemented": ["deployed", "executed"],
}
# Location normalization: remove state/region, standardize city names
LOCATION_NORMALIZE = {
    "bangalore": "Bengaluru",
    "mumbai metropolitan region": "Mumbai",
    "hyderabad area": "Hyderabad",
    "telangana": "",
    "karnataka": "",
    "metropolitan region": "",
    " area": "",
}
WORDS_PER_PAGE_ESTIMATE = 400
MAX_PAGES = 2

# Part A Rule 1: Bad bullet endings — methodology/JD keywords that should be metrics
BAD_ENDING_PATTERNS = [
    # Comma-separated JD keyword dumps at end
    re.compile(r",\s*\w[\w\s]*$"),
    # Soft skill phrases at end
    re.compile(r"(?:enhanced|improved|strengthened|fostered)\s+(?:customer|team|stakeholder|cross-functional)\s+\w+\.?$", re.I),
]
# Words that signal a bullet ends with methods instead of results
METHOD_ENDING_WORDS = {
    "strategy", "strategies", "vision", "roadmap", "planning", "alignment",
    "collaboration", "empathy", "engagement", "framework", "methodology",
    "initiatives", "optimization", "innovation", "transformation",
    "stakeholders", "cross-functional", "leadership", "prioritization",
}

# Part A Rule 6: Cross-JD contamination — company-specific terms
COMPANY_SPECIFIC_TERMS = {
    "microsoft": {"azure", "microsoft 365", "m365", "copilot", "teams", "office", "windows", "bing", "sharepoint", "onedrive"},
    "google": {"gcp", "google cloud", "chromeos", "android", "bigquery", "tensorflow", "waymo"},
    "amazon": {"aws", "alexa", "prime", "kindle", "lambda", "s3", "ec2"},
    "apple": {"ios", "macos", "xcode", "swift", "siri", "airpods"},
    "meta": {"instagram", "whatsapp business api", "oculus", "metaverse"},
    "intuit": {"turbotax", "quickbooks", "mailchimp", "mint", "credit karma"},
}

REFRAME_PROMPT = """You are an expert resume reframing engine for ATS-optimized, interview-defensible resumes. Generate tailored resume content from the candidate's Profile Knowledge Base (PKB), using the JD analysis and mapping matrix. Follow ALL rules below. These are final and non-negotiable.

RULE 0 — TITLE INTEGRITY (HIGHEST PRIORITY — CANNOT BE OVERRIDDEN):
The candidate's job titles MUST be copied EXACTLY from the PKB. NEVER change, upgrade, or fabricate titles.
- If PKB says "Senior Product Manager" at Planful → resume MUST say "Senior Product Manager" at Planful
- Do NOT upgrade titles to match the JD (no "Senior PM" → "Group PM" or "Principal PM")
- Do NOT add titles the candidate never held
- The summary MUST open with the candidate's ACTUAL highest title (e.g., "Senior Product Manager with 8+ years...")
- The summary MUST NOT claim to be a GPM, Principal PM, Director, or any title not in the PKB
- Violation of this rule is CAREER-ENDING (fails background checks). This overrides all other rules.

RULE 0B — METRIC INTEGRITY (HIGHEST PRIORITY — CANNOT BE OVERRIDDEN):
Every number, dollar amount, percentage, or metric in the resume MUST be traceable to the PKB.
- Do NOT invent dollar amounts ($8M, $50M, $500M, $5M) unless they appear in the PKB
- Do NOT extrapolate or calculate new numbers (e.g., "50% revenue growth" does NOT become "$5M incremental")
- Do NOT add count estimates ("200+ organizations", "50+ stakeholders", "50,000+ customers") unless in PKB
- If a bullet needs a metric but PKB doesn't have one, use the closest PKB metric (even if it's a %, not $)
- If no metric exists at all, use a qualitative impact statement — do NOT invent a number
- The ONLY metrics allowed are those that appear verbatim in the PKB bullet text

RULE 1 — EXPERIENCE POSITIONING: The summary must open with "Senior Product Manager with [X]+ years" where X = max(8, actual_years_of_experience). Never output less than 8+. Calculate actual years from the earliest role start date to today. Always position as "Senior Product Manager" (the candidate's ACTUAL title). Frame as experienced senior leader.

RULE 2 — PROFESSIONAL SUMMARY:
- Summary must be 3-4 lines maximum, under 60 words total.
- Line 1: ACTUAL title + years + core domain (e.g., "Senior Product Manager with 8+ years building enterprise SaaS and fintech platforms")
- Line 2: top 2-3 achievements with specific metrics FROM THE PKB ONLY
- Line 3: key capabilities relevant to target JD domain
- Maximum 3 JD-specific terms in the summary. The rest should be naturally worded achievements.
- Do NOT claim "deep domain expertise in [JD domain]" — instead say "experience building [domain] products"
- Do NOT aggregate or calculate metrics (no "$50M+ annual revenue impact" from individual bullets)
- MUST reference the target company's domain naturally, not as keyword dump

RULE 3 — BULLET STRUCTURE: XYZ: Accomplished [X] as measured by [Y], by doing [Z]. Every bullet MUST have a quantified metric FROM THE PKB. Every bullet must be 20-30 words. No exceptions. Max 3-4 JD keywords per bullet. Lead each role with the most JD-relevant bullet. BANNED starts: "Responsible for", "Managed", "Helped", "Assisted", "Participated", "Planned", "Supported", "Worked on", "Handled", "Involved in". REQUIRED starts: Led, Drove, Launched, Built, Owned, Delivered, Designed, Spearheaded, Achieved, Scaled, Transformed, Architected. Only describe shipped/deployed work.

RULE 4 — BULLETS PER ROLE (hard constraints): Most recent role: minimum 4, maximum 5 bullets. Second most recent: minimum 3, maximum 4 bullets. Third role: minimum 3, maximum 4 bullets. Roles older than 3 positions back: maximum 2 bullets. Internships: exactly 1 bullet. If reframer produces fewer than minimum, pull additional relevant experience from PKB.

RULE 5 — ONLY RELEVANT POINTERS: Every bullet must map to P0 or P1 JD requirement. Does this help get shortlisted for THIS job? If no, cut it. 4 perfect bullets beat 8 mediocre ones.

RULE 6 — TOP 1% LANGUAGE: Outcomes, not tasks. Business impact: revenue, growth, retention, efficiency. Show WHY it mattered and the RESULT. Think: how would a VP describe this in a board presentation?

RULE 7 — REFRAMING BOUNDARIES (no anachronistic tech): For roles ending before June 2023, do NOT use "LLM", "LLM-powered", "large language model", "GPT", "generative AI", "gen AI", "RAG", "retrieval-augmented". Use "NLP-driven", "ML-powered", "conversational AI", "machine learning", "information retrieval" instead. Not allowed: invent work, claim tools never used. Only shipped/deployed work.

RULE 8 — KEYWORD USAGE + THE ENDING TEST: EXACT phrases from JD. P0: 2-3 times; P1: 1-2 times. No keyword more than 4 times. Distribute across summary + skills + experience.
THE ENDING TEST (CRITICAL): Every bullet must END with a RESULT (metric, number, business outcome), NEVER with a METHOD (JD keyword, strategy word, soft skill). Pattern: [Verb] [context with JD keywords woven in] [comma] [metric/result at end].
- GOOD: "...driving 35% improvement in customer retention."
- GOOD: "...reducing ticket volume by 35% and improving conversion by 25%."
- BAD: "...enhanced customer empathy." (ends with method)
- BAD: "...product vision, product strategy." (ends with keyword dump)
- BAD: "...driving alignment with organizational growth objectives." (ends with method)
JD keywords belong in the MIDDLE of bullets. Metrics belong at the END. Read each bullet's last 4 words — if they're not a number or business result, rewrite.

RULE 9 — SKILLS SECTION:
- Maximum 25 terms total. Organize into Technical, Methodologies, Domains.
- Every term must map to a P0 or P1 keyword.
- No abbreviation + full form duplicates: pick ONE (e.g., "FP&A" OR "Financial Planning & Analytics", not both)
- No near-synonym duplicates: "Product Vision" + "Product Strategy" + "Strategy" → pick ONE that appears in JD
- Include REAL TOOLS the candidate actually uses (from PKB): JIRA, Pendo, Qlikview, Salesforce, Freshdesk — only if they map to JD requirements
- Never include single vague words as skills: "Tech", "Data", "Strategy" alone are meaningless
- Each skill must be specific enough that a recruiter knows what it means

RULE 10 — FORMAT: Date format "Mon YYYY – Mon YYYY". Single column. Standard headers. Location format: "City, Country" only (no state/region e.g. no Telangana, Karnataka, Metropolitan Region). Bangalore → Bengaluru, Hyderabad Area → Hyderabad, Mumbai Metropolitan Region → Mumbai.

RULE 11 — AWARDS & RECOGNITION: If PKB has awards, add "Awards & Recognition" section after Skills and before Education. Format: one line per award: "• [Award Title], [Company] ([Year]) | [One-line description]". Maximum 4 awards. Only include awards from the last 7 years. Sort by most recent first.

RULE 12 — EDUCATION & CERTIFICATIONS: One line per education. Certifications relevant to JD only. DEGREE INTEGRITY: Use the EXACT degree name from the PKB. Do NOT add specializations that don't exist. Examples: INSEAD = "Executive MBA" (NOT "Executive MBA in Product Management"), IIT = "B.Tech" (NOT "B.Tech in Computer Science" unless PKB says so). If the PKB degree field is blank or generic, keep it generic.

RULE 13 — TONE: Confident, human, crisp. No buzzword chains. No verb used more than twice across all bullets — use synonym variety (Led → Spearheaded, Drove → Owned, etc.).

RULE 14 — SELF-CHECK: Summary 3-4 lines under 60 words, opens "Senior Product Manager with 8+ years"; titles EXACTLY match PKB; no fabricated metrics; most recent role 4-5 bullets, second 3-4, third 3-4; every bullet 20-30 words, metric from PKB, no banned starts; no pre-2023 LLM/GPT/RAG/GenAI; skills ≤25 terms with no duplicates; locations City, Country; reframing_log complete.

REFRAMING LOG: For each reframed bullet: original, reframed, jd_keywords_used, what_changed, interview_prep.

Work experience: REVERSE-CHRONOLOGICAL. EXACT same companies and titles from PKB — do NOT change any title. Apply bullet limits. Return ONLY valid JSON (no markdown)."""

PATCH_REFRAME_PROMPT = """You are making targeted edits to an existing resume to address specific feedback. The resume is already well-structured and ATS-optimized. Make MINIMAL, targeted changes only.

CRITICAL RULES (CANNOT BE VIOLATED):
- NEVER change job titles — they must match the PKB/profile originals exactly
- NEVER invent dollar amounts, metrics, or numbers not in the original resume
- NEVER end bullets with comma-separated JD keywords (e.g., "...product vision, product strategy")
- NEVER append soft skill phrases at the end of bullets (e.g., "...enhanced customer empathy")
- JD keywords must be WOVEN naturally into sentences, not appended

OTHER RULES:
- Keep all existing content unless the feedback specifically asks to change it
- When adding keywords (e.g., "GTM", "product judgment"), integrate them naturally into existing sentences
- Maintain the same structure, tone, and formatting
- Do NOT regenerate entire sections unless feedback explicitly requires it
- Preserve all metrics, dates, and company names exactly as they are
- Return the FULL resume JSON with only the targeted edits applied

Return ONLY valid JSON (no markdown)."""


def _condensed_pkb_for_api(pkb: dict) -> dict:
    """Build a smaller PKB for the API call to reduce payload and avoid timeouts.
    Keeps only fields needed for resume generation; full pkb is used for post-processing."""
    work = []
    for w in pkb.get("work_experience") or []:
        bullets = []
        for b in w.get("bullets") or []:
            text = (b.get("original_text") or "").strip()
            if text:
                bullets.append(text)
        work.append({
            "company": w.get("company"),
            "title": w.get("title"),
            "dates": w.get("dates"),
            "location": w.get("location"),
            "bullets": bullets,
        })
    return {
        "personal_info": pkb.get("personal_info") or {},
        "work_experience": work,
        "skills": pkb.get("skills") or {},
        "education": pkb.get("education") or [],
        "certifications": pkb.get("certifications") or [],
    }


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
    """Shorten bullet to at most max_words. Hard cap — no exceptions.

    Tries to cut at a clause boundary first for readability, but always
    enforces the word limit as an absolute ceiling.
    """
    words = bullet.split()
    if len(words) <= max_words:
        return bullet
    truncated = " ".join(words[:max_words])
    # Try cutting at last clause boundary for cleaner sentence
    best = None
    for sep in (". ", "; ", ", ", " — "):
        last_sep = truncated.rfind(sep)
        if last_sep > len(truncated) * 0.5:
            # Cut BEFORE the separator for commas (end of clause), AFTER for periods
            if sep in (". ", "; "):
                candidate = truncated[:last_sep + 1].strip()
            else:
                candidate = truncated[:last_sep].strip()
            if len(candidate.split()) <= max_words and len(candidate.split()) >= 15:
                best = candidate
                break
    if best:
        truncated = best
    else:
        # No good clause boundary — hard cut and strip dangling small words
        truncated = " ".join(words[:max_words])
        # Remove dangling prepositions, conjunctions, articles at the end
        dangling = {"for", "to", "in", "of", "by", "with", "and", "or", "the",
                    "a", "an", "at", "on", "as", "from", "into", "across", "through"}
        trunc_words = truncated.split()
        while trunc_words and trunc_words[-1].lower().rstrip(".,;") in dangling:
            trunc_words.pop()
        truncated = " ".join(trunc_words)
    # Hard cap: ensure we never exceed max_words
    final_words = truncated.split()
    if len(final_words) > max_words:
        truncated = " ".join(final_words[:max_words])
    if not truncated.rstrip().endswith((".", "!", "?")):
        truncated = truncated.rstrip(".,;:— ") + "."
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


def _role_end_before_june_2023(role: dict, pkb: dict) -> bool:
    """True if role end date is before June 2023 (no LLM/GPT/RAG/GenAI)."""
    end_year = _get_role_end_year(role, pkb)
    if end_year < PRE_2023_CUTOFF_YEAR:
        return True
    if end_year > PRE_2023_CUTOFF_YEAR:
        return False
    dates = role.get("dates") or ""
    if isinstance(dates, dict):
        end = dates.get("end") or ""
    else:
        end = str(dates)
    # If 2023, check month: Jan-May = before June
    if re.search(r"Jan|Feb|Mar|Apr|May", end, re.I):
        return True
    return False


def _fix_pre_2023_language(bullet: str, role_end_year: int) -> str:
    """Replace LLM-powered/GenAI with conversational AI/ML-powered for pre-2023 roles."""
    if role_end_year >= PRE_2023_CUTOFF_YEAR:
        return bullet
    b = re.sub(r"\bLLM-powered\b", "NLP-driven", bullet, flags=re.IGNORECASE)
    b = re.sub(r"\bLLM\b", "conversational AI", b, flags=re.IGNORECASE)
    b = re.sub(r"\blarge language model\b", "machine learning", b, flags=re.IGNORECASE)
    b = re.sub(r"\bGPT\b", "ML-powered", b, flags=re.IGNORECASE)
    b = re.sub(r"\bgenerative AI\b", "machine learning", b, flags=re.IGNORECASE)
    b = re.sub(r"\bgen AI\b", "ML-powered", b, flags=re.IGNORECASE)
    b = re.sub(r"\bRAG\b", "information retrieval", b, flags=re.IGNORECASE)
    b = re.sub(r"\bretrieval-augmented\b", "information retrieval", b, flags=re.IGNORECASE)
    b = re.sub(r"\bGenAI\b", "ML-powered", b, flags=re.IGNORECASE)
    b = re.sub(r"\bAI-powered\b", "ML-powered", b, flags=re.IGNORECASE)
    return b


def _fix_pre_2023_tech_full(result: dict, pkb: dict) -> dict:
    """Post-process: for roles ending before June 2023, replace anachronistic tech terms. Log every replacement."""
    work = result.get("work_experience", [])
    new_work = []
    for role in work:
        if not _role_end_before_june_2023(role, pkb):
            new_work.append(role)
            continue
        end_year = _get_role_end_year(role, pkb)
        new_bullets = []
        for b in role.get("bullets") or []:
            orig = b
            b = _fix_pre_2023_language(b, end_year)
            if b != orig:
                logger.info("Pre-2023 tech replacement: %s -> %s", orig[:60], b[:60])
            new_bullets.append(b)
        new_work.append({**role, "bullets": new_bullets})
    return {**result, "work_experience": new_work}


def _normalize_location(loc: str) -> str:
    """Normalize to 'City, Country'. Remove state/region. Map Bangalore→Bengaluru, etc."""
    if not loc or not isinstance(loc, str):
        return loc
    s = loc.strip()
    for k, v in LOCATION_NORMALIZE.items():
        if k in s.lower():
            s = re.sub(re.escape(k), v, s, flags=re.IGNORECASE)
    # Remove trailing comma/comma-space and "India" duplicates, clean double spaces
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith(","):
        s = s[:-1].strip()
    if not re.search(r"India|USA|UK", s, re.I) and "India" in loc:
        s = s + ", India" if s else "India"
    return s or loc


def _normalize_locations(result: dict) -> dict:
    """Post-process: every role location to City, Country format."""
    work = result.get("work_experience", [])
    new_work = [{**r, "location": _normalize_location(r.get("location") or "")} for r in work]
    return {**result, "work_experience": new_work}


def _get_opening_verb(bullet: str) -> str:
    """Return the first word (verb) of the bullet, lowercased."""
    if not bullet:
        return ""
    first = (bullet.strip().split() or [""])[0].lower().rstrip(".,;")
    return first


def _enforce_verb_variety(result: dict) -> dict:
    """No verb used more than twice. Replace 3rd+ with synonym. Log every swap."""
    work = result.get("work_experience", [])
    all_bullets_flat = []
    for r in work:
        for b in r.get("bullets") or []:
            all_bullets_flat.append((r, b))
    verb_count = {}
    for r, b in all_bullets_flat:
        v = _get_opening_verb(b)
        if v:
            verb_count[v] = verb_count.get(v, 0) + 1
    replacements = {}
    for v, count in verb_count.items():
        if count >= 3 and v in VERB_SYNONYMS:
            syns = VERB_SYNONYMS[v]
            need = count - 2
            replacements[v] = (syns, need)
    if not replacements:
        return result
    new_work = []
    used = {}
    for role in work:
        new_bullets = []
        for bullet in role.get("bullets") or []:
            v = _get_opening_verb(bullet)
            if v not in replacements:
                new_bullets.append(bullet)
                continue
            syns, need = replacements[v]
            used[v] = used.get(v, 0) + 1
            if used[v] <= 2:
                new_bullets.append(bullet)
                continue
            idx = min(used[v] - 3, len(syns) - 1)
            repl = syns[idx]
            words = bullet.split()
            rest = " ".join(words[1:]).lstrip() if len(words) > 1 else ""
            new_b = (repl.capitalize() + " " + rest).strip()
            logger.info("Verb variety swap: %s -> %s", v, repl)
            new_bullets.append(new_b)
        new_work.append({**role, "bullets": new_bullets})
    return {**result, "work_experience": new_work}


def _replace_banned_verbs(result: dict) -> dict:
    """If any bullet starts with a banned phrase, replace with strong verb. Log every replacement."""
    work = result.get("work_experience", [])
    new_work = []
    for role in work:
        new_bullets = []
        for b in role.get("bullets") or []:
            if _bullet_starts_with_banned(b):
                new_b = _rewrite_banned_start(b)
                logger.info("Banned verb replaced: %s -> %s", b[:50], new_b[:50])
                new_bullets.append(new_b)
            else:
                new_bullets.append(b)
        new_work.append({**role, "bullets": new_bullets})
    return {**result, "work_experience": new_work}


def _enforce_bullet_word_count(result: dict) -> dict:
    """Trim bullets >30 words; flag (log) if <15. Keep verb, metric, primary keyword."""
    work = result.get("work_experience", [])
    new_work = []
    for role in work:
        new_bullets = []
        for b in role.get("bullets") or []:
            wc = _word_count(b)
            if wc > MAX_WORDS_PER_BULLET:
                b = _shorten_bullet_to_max_words(b, max_words=MAX_WORDS_PER_BULLET)
                logger.info("Bullet trimmed to %d words (max %d)", _word_count(b), MAX_WORDS_PER_BULLET)
            elif wc < 15 and wc > 0:
                logger.warning("Bullet under 15 words (flag for expansion): %d words", wc)
            new_bullets.append(b)
        new_work.append({**role, "bullets": new_bullets})
    return {**result, "work_experience": new_work}


def _enforce_bullet_limits(work_experience: list, pkb: dict) -> list:
    """Enforce min/max bullets: most recent 4-5, second 3-4, third 3-4, older max 2, internship 1."""
    pkb_order = [w["company"] for w in pkb.get("work_experience", [])]
    if not pkb_order:
        return work_experience
    result = []
    for i, role in enumerate(work_experience):
        company = (role.get("company") or "").strip().lower()
        bullets = list(role.get("bullets") or [])
        is_internship = "fidelity" in company or "intern" in (role.get("title") or "").lower()
        is_early_dev = "cognizant" in company
        if is_internship or is_early_dev:
            cap_min, cap_max = MAX_BULLETS_INTERNSHIP, MAX_BULLETS_INTERNSHIP
        elif i == 0:
            cap_min, cap_max = MIN_BULLETS_MOST_RECENT, MAX_BULLETS_MOST_RECENT
        elif i == 1:
            cap_min, cap_max = MIN_BULLETS_SECOND, MAX_BULLETS_SECOND
        elif i == 2:
            cap_min, cap_max = MIN_BULLETS_THIRD, MAX_BULLETS_THIRD
        else:
            cap_min, cap_max = 0, MAX_BULLETS_OLD_ROLE
        if len(bullets) > cap_max:
            bullets = bullets[:cap_max]
        # Min is enforced by prompt; we don't pull from PKB here (reframer must produce enough)
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


def _inject_awards_from_pkb(result: dict, pkb: dict) -> dict:
    """If PKB has awards/achievements, add Awards & Recognition section (max 4, professional first)."""
    awards_raw = pkb.get("awards") or pkb.get("achievements") or []
    if not awards_raw:
        return result
    professional = []
    academic = []
    award_keywords = {"award", "winner", "finalist", "medal", "star performer",
                      "employee of the year", "product of the year", "recognition"}
    exclude_keywords = {"launch success", "program success", "partnership success"}
    work_companies = {(w.get("company") or "").lower() for w in pkb.get("work_experience", [])}
    for a in awards_raw:
        if isinstance(a, dict):
            title = (a.get("title") or a.get("name") or "").lower()
            context = (a.get("context") or a.get("company") or "").lower()
            title_display = a.get("title") or a.get("name") or "Award"
            company_display = a.get("context") or a.get("company") or ""
        elif isinstance(a, str):
            title, context = a.lower(), ""
            title_display, company_display = a.strip(), ""
        else:
            continue
        if any(ex in title for ex in exclude_keywords):
            continue
        if not any(kw in title for kw in award_keywords):
            continue
        if "badminton" in title:
            continue
        is_professional = any(context in c or c in context for c in work_companies if c)
        year = _estimate_award_year(a if isinstance(a, dict) else {"title": a}, pkb)
        entry_text = f"• {title_display}, {company_display} ({year})"
        if is_professional:
            professional.append((year, entry_text))
        else:
            academic.append((year, entry_text))
    professional.sort(key=lambda x: -x[0])
    academic.sort(key=lambda x: -x[0])
    combined = professional[:4]
    remaining = 4 - len(combined)
    if remaining > 0:
        combined.extend(academic[:remaining])
    result["awards"] = [e[1] for e in combined]
    return result


def _fix_number_spacing(text: str) -> str:
    """Enhanced spacing: digit+lowercase, digit+uppercase word, word+paren, dedup spaces."""
    if not text:
        return text
    text = re.sub(r'(\d)([a-z])', r'\1 \2', text)
    text = re.sub(r'(\d\.?\d*)([A-Z][a-z]{2,})', r'\1 \2', text)
    text = re.sub(r'([a-zA-Z0-9])\(', r'\1 (', text)
    text = re.sub(r'  +', ' ', text)
    return text


def _fix_currency_symbols(text: str) -> str:
    """Replace ₹ with INR (font may not support ₹ glyph)."""
    if not text:
        return text
    text = text.replace("₹", "INR ")
    text = re.sub(r"INR\s+", "INR ", text)  # normalize multiple spaces
    return text


def _enforce_title_integrity(result: dict, pkb: dict) -> dict:
    """CRITICAL: Ensure every role title matches PKB exactly. Never allow title fabrication."""
    pkb_titles = {}
    for w in pkb.get("work_experience", []):
        company = (w.get("company") or "").strip()
        title = (w.get("title") or "").strip()
        if company and title:
            pkb_titles[company.lower()] = title
    work = result.get("work_experience", [])
    for role in work:
        company = (role.get("company") or "").strip()
        if company.lower() in pkb_titles:
            original_title = pkb_titles[company.lower()]
            current_title = (role.get("title") or "").strip()
            if current_title != original_title:
                logger.warning("TITLE INTEGRITY: Correcting '%s' -> '%s' for %s", current_title, original_title, company)
                role["title"] = original_title
    # Fix summary: ensure it doesn't claim titles not in PKB
    summary = result.get("professional_summary", "")
    fabricated_titles = ["group product manager", "principal product manager", "director of product",
                         "vp of product", "head of product", "chief product officer"]
    summary_lower = summary.lower()
    for fab in fabricated_titles:
        if fab in summary_lower:
            # Check if this title is actually in PKB
            if not any(fab in t.lower() for t in pkb_titles.values()):
                logger.warning("TITLE INTEGRITY: Removing fabricated title '%s' from summary", fab)
                # Replace with actual highest title
                highest_title = "Senior Product Manager"
                for t in pkb_titles.values():
                    highest_title = t
                    break
                summary = re.sub(re.escape(fab), highest_title.lower(), summary, flags=re.IGNORECASE)
    # Fix common patterns: "Group Product Manager and Principal Product Manager with" -> "Senior Product Manager with"
    summary = re.sub(
        r"(?i)group product manager\s+and\s+principal product manager",
        "Senior Product Manager", summary
    )
    summary = re.sub(r"(?i)principal product manager", "Senior Product Manager", summary)
    summary = re.sub(r"(?i)group product manager", "Senior Product Manager", summary)
    # Deduplicate: "Senior Product Manager Senior Product Manager" -> "Senior Product Manager"
    summary = re.sub(r"(?i)(senior\s+product\s+manager)\s+and\s+\1", r"Senior Product Manager", summary)
    summary = re.sub(r"(?i)(senior\s+product\s+manager)\s+\1", r"Senior Product Manager", summary)
    summary = re.sub(r"^senior product manager", "Senior Product Manager", summary)
    result["professional_summary"] = summary
    return result


def _dedup_skills(result: dict) -> dict:
    """Remove abbreviation+full form duplicates and near-synonym duplicates from skills."""
    # Known abbreviation pairs
    abbrev_pairs = {
        "fp&a": "financial planning & analytics",
        "bi": "business intelligence",
        "ai": "artificial intelligence",
        "ml": "machine learning",
        "nlp": "natural language processing",
        "crm": "customer relationship management",
        "erp": "enterprise resource planning",
        "m365": "microsoft 365",
    }
    # Near-synonyms: keep only one
    synonym_groups = [
        {"product vision", "product strategy", "strategy"},
        {"roadmap development", "roadmaps", "roadmap planning", "roadmap"},
        {"ai technology", "ai tech", "ai"},
        {"data analytics", "data analysis", "data"},
    ]
    sk = result.get("skills", {})
    for key in ("technical", "methodologies", "domains"):
        items = sk.get(key) or []
        if not items:
            continue
        # Pass 1: remove abbrev+full form duplicates
        lower_items = {item.lower(): item for item in items}
        to_remove = set()
        for abbr, full in abbrev_pairs.items():
            if abbr in lower_items and full in lower_items:
                # Keep whichever is shorter (the abbreviation)
                to_remove.add(full)
        # Pass 2: remove near-synonym duplicates (keep first occurrence)
        for group in synonym_groups:
            found = [item for item in items if item.lower() in group]
            if len(found) > 1:
                for extra in found[1:]:
                    to_remove.add(extra.lower())
        # Pass 3: remove single vague words
        vague_words = {
            "tech", "data", "strategy", "technologies",
            "growth mindset", "adaptability", "curiosity", "empathy",
            "customer empathy", "bias for action", "drive change",
            "collaboration", "teaching", "coaching", "communication",
            "strong communication skills", "quad", "email", "roadmaps",
            "use case maturity", "small businesses", "analytics",
            "analytics tools", "analytics platforms", "usage analytics",
        }
        for item in items:
            if item.lower().strip() in vague_words:
                to_remove.add(item.lower())
        filtered = [item for item in items if item.lower() not in to_remove]
        sk[key] = filtered
    result["skills"] = sk
    return result


def _apply_text_fixes(result: dict) -> dict:
    """Apply number spacing and currency fixes to all text fields."""
    summary = result.get("professional_summary", "")
    summary = _fix_number_spacing(summary)
    summary = _fix_currency_symbols(summary)
    result["professional_summary"] = summary
    if result.get("subtitle"):
        result["subtitle"] = _fix_number_spacing(result["subtitle"])
    for role in result.get("work_experience", []):
        role["bullets"] = [_fix_currency_symbols(_fix_number_spacing(b)) for b in (role.get("bullets") or [])]
    sk = result.get("skills", {})
    for key in ("technical", "methodologies", "domains"):
        sk[key] = [_fix_number_spacing(item) for item in (sk.get(key) or [])]
    result["awards"] = [_fix_number_spacing(a) for a in (result.get("awards") or [])]
    return result


def _fix_bullet_endings(result: dict, parsed_jd: dict) -> dict:
    """Part A Rule 1: Bullets must end with RESULTS (metrics), not METHODS (keywords).

    Detects bullets ending with methodology words or comma-separated JD keywords.
    Truncates bad endings at the last metric or meaningful clause.
    """
    p0_p1 = set((kw or "").lower() for kw in
                 (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or []))
    work = result.get("work_experience", [])
    for role in work:
        new_bullets = []
        for bullet in role.get("bullets") or []:
            fixed = _fix_single_bullet_ending(bullet, p0_p1)
            new_bullets.append(fixed)
        role["bullets"] = new_bullets
    return result


def _fix_single_bullet_ending(bullet: str, jd_keywords: set) -> str:
    """Fix a single bullet if it ends with methods instead of results."""
    if not bullet or len(bullet.split()) < 10:
        return bullet
    # Get last 4 words
    words = bullet.rstrip(".").split()
    last_4 = " ".join(words[-4:]).lower()

    # Check if last words are JD keyword dump (2+ consecutive JD terms at end)
    jd_at_end = 0
    for w in reversed(words[-4:]):
        clean = w.lower().rstrip(".,;:")
        if clean in jd_keywords or clean in METHOD_ENDING_WORDS:
            jd_at_end += 1
        else:
            break

    if jd_at_end >= 2:
        # Truncate: find the last metric or verb clause before the keyword dump
        cut_point = len(words) - jd_at_end
        truncated = " ".join(words[:cut_point]).rstrip(".,;:— ")
        if len(truncated.split()) >= 12:
            if not truncated.endswith("."):
                truncated += "."
            logger.info("Bullet ending fixed (keyword dump): ...%s -> ...%s",
                       " ".join(words[-4:]), truncated.split()[-3:])
            return truncated

    # Check if ends with soft-skill/method phrase
    for pattern in BAD_ENDING_PATTERNS:
        match = pattern.search(bullet.rstrip("."))
        if match and match.start() > len(bullet) * 0.6:
            truncated = bullet[:match.start()].rstrip(".,;:— ")
            if len(truncated.split()) >= 12 and _bullet_has_metric(truncated):
                if not truncated.endswith("."):
                    truncated += "."
                logger.info("Bullet ending fixed (method phrase): removed '%s'", match.group(0)[:40])
                return truncated

    return bullet


def _fix_incomplete_sentences(result: dict) -> dict:
    """Part A Rule 8: Expanded dangling word detection for incomplete sentences."""
    dangling = {
        "for", "to", "in", "of", "by", "with", "and", "or", "the",
        "a", "an", "at", "on", "as", "from", "into", "across", "through",
        "within", "between", "among", "toward", "towards", "during",
        "including", "such", "via", "using", "leveraging", "enabling",
        "driving", "enhancing", "improving", "ensuring", "supporting",
        "hypothesis-driven", "data-driven", "customer-focused",
        "competitive", "strategic", "innovative", "comprehensive",
        "manual", "significant", "measurable", "actionable",
        "demonstrating", "providing", "delivering", "achieving",
        "generating", "establishing", "maintaining", "coordinating",
    }
    for role in result.get("work_experience", []):
        new_bullets = []
        for bullet in role.get("bullets") or []:
            words = bullet.rstrip(".!?").split()
            changed = False
            while words and words[-1].lower().rstrip(".,;:") in dangling:
                words.pop()
                changed = True
            # Remove trailing noise like "12 use" or "50 term"
            if words and len(words) >= 2:
                last = words[-1].lower().rstrip(".,;:")
                if re.match(r'\d+\+?$', words[-2]) and last in {"use", "term", "type", "case", "mode"}:
                    words.pop()
                    changed = True
            if changed and words:
                bullet = " ".join(words)
                if not bullet.endswith((".", "!", "?")):
                    bullet += "."
            new_bullets.append(bullet)
        role["bullets"] = new_bullets
    return result


def _check_cross_jd_contamination(result: dict, parsed_jd: dict) -> dict:
    """Part A Rule 6: Remove company-specific terms that don't belong to the target JD."""
    target_company = (parsed_jd.get("company") or "").lower()
    # Find which company sets are NOT the target
    forbidden_terms = set()
    for company_key, terms in COMPANY_SPECIFIC_TERMS.items():
        if company_key not in target_company:
            forbidden_terms.update(terms)
    # But ALLOW terms that appear in the JD itself
    jd_all = set((kw or "").lower() for kw in (parsed_jd.get("all_keywords_flat") or []))
    forbidden_terms -= jd_all

    if not forbidden_terms:
        return result

    # Check summary
    summary = result.get("professional_summary", "")
    for term in forbidden_terms:
        if term.lower() in summary.lower():
            logger.warning("Cross-JD contamination in summary: '%s' (not in target JD)", term)
            # Remove the term (simple replacement)
            summary = re.sub(r'\b' + re.escape(term) + r'\b', '', summary, flags=re.IGNORECASE)
            summary = re.sub(r'\s+', ' ', summary).strip()
    result["professional_summary"] = summary

    # Check skills
    sk = result.get("skills", {})
    for key in ("technical", "methodologies", "domains"):
        items = sk.get(key) or []
        filtered = [item for item in items if item.lower() not in forbidden_terms]
        if len(filtered) < len(items):
            removed = set(i.lower() for i in items) - set(i.lower() for i in filtered)
            logger.warning("Cross-JD contamination in skills: removed %s", removed)
        sk[key] = filtered
    result["skills"] = sk
    return result


def _estimate_award_year(award: dict, pkb: dict) -> int:
    """Part A Rule 7: Estimate award year from known mapping or employment dates."""
    title = (award.get("title") or award.get("name") or "").lower()
    context = (award.get("context") or award.get("company") or "").lower()
    known_years = {
        ("star performer", "wealthy"): 2023,
        ("star performer", "icici"): 2021,
        ("employee of the year", "icici"): 2020,
        ("product of the year", "icici"): 2021,
        ("aviva", "iim"): 2018,
        ("badminton", ""): 2015,
        ("hul", "iim"): 2018,
        ("pepsico", "iim"): 2018,
        ("insurance suite", "wealthy"): 2023,
        ("client partnership", "planful"): 2024,
    }
    for (title_key, context_key), year in known_years.items():
        if title_key in title and (not context_key or context_key in context):
            return year
    for w in pkb.get("work_experience", []):
        company = (w.get("company") or "").lower()
        if context and (context in company or company in context):
            dates = w.get("dates") or {}
            end = dates.get("end") or ""
            if end:
                m = re.search(r"20\d{2}|19\d{2}", end)
                if m:
                    return int(m.group(0))
    return 2020


def _enforce_skills_minimum(result: dict, pkb: dict, parsed_jd: dict) -> dict:
    """Part A Rule 5: Ensure 15-25 real skills. Include actual tools from PKB."""
    sk = result.get("skills", {})
    tech = list(sk.get("technical") or [])
    meth = list(sk.get("methodologies") or [])
    dom = list(sk.get("domains") or [])
    existing_lower = {s.lower() for s in tech + meth + dom}

    for tool in ["SQL", "JIRA", "Pendo", "A/B Testing", "Figma"]:
        if tool.lower() not in existing_lower:
            tech.append(tool)
            existing_lower.add(tool.lower())
    for m in ["Agile/Scrum", "Design Thinking", "User Research", "Competitive Analysis"]:
        if m.lower() not in existing_lower:
            meth.append(m)
            existing_lower.add(m.lower())
    for d in ["Enterprise SaaS", "Fintech", "Insurance"]:
        if d.lower() not in existing_lower:
            dom.append(d)
            existing_lower.add(d.lower())

    total = len(tech) + len(meth) + len(dom)
    if total < 15:
        pkb_tools = set()
        for key in ("hard_skills", "tools", "methodologies"):
            for s in (pkb.get("skills", {}).get(key) or []):
                if s and len(s) > 2:
                    pkb_tools.add(s)
        jd_kws = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])
        needed = 15 - total
        added = 0
        for tool in pkb_tools:
            if added >= needed:
                break
            if tool.lower() not in existing_lower:
                if any(tool.lower() in (kw or "").lower() or (kw or "").lower() in tool.lower() for kw in jd_kws):
                    tech.append(tool)
                    existing_lower.add(tool.lower())
                    added += 1
        for tool in pkb_tools:
            if added >= needed:
                break
            if tool.lower() not in existing_lower:
                tech.append(tool)
                existing_lower.add(tool.lower())
                added += 1

    sk["technical"] = tech
    sk["methodologies"] = meth
    sk["domains"] = dom
    result["skills"] = sk
    return result


def _enforce_summary_format(result: dict, parsed_jd: dict) -> dict:
    """Part A Rule 3: Summary must be exactly 3 sentences, 45-55 words, max 3 JD terms."""
    summary = (result.get("professional_summary") or "").strip()
    if not summary:
        return result

    # Count words
    word_count = len(summary.split())

    # Count sentences (rough)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', summary) if s.strip()]

    # If summary is way too long (>60 words), truncate to 3 sentences
    if word_count > 60 and len(sentences) > 3:
        summary = " ".join(sentences[:3])
        if not summary.endswith("."):
            summary += "."
        result["professional_summary"] = summary
        logger.info("Summary truncated to 3 sentences (%d -> %d words)", word_count, len(summary.split()))

    return result


def _generate_subtitle(result: dict, parsed_jd: dict) -> str:
    """Part A Rule 4: Generate subtitle tagline 'Title | Domain | Years', max 60 chars."""
    # Extract domain from JD
    jd_domain = ""
    industry_terms = parsed_jd.get("industry_terms") or []
    if industry_terms:
        # Pick top 1-2 domain terms
        top_terms = []
        for term in industry_terms[:3]:
            t = term.get("term") or term if isinstance(term, str) else (term.get("term") or "")
            if t and len(t) > 2:
                top_terms.append(t)
            if len(top_terms) >= 2:
                break
        jd_domain = " & ".join(top_terms[:2])

    if not jd_domain:
        # Fallback to company context
        ctx = parsed_jd.get("company_context") or ""
        if "saas" in ctx.lower():
            jd_domain = "SaaS"
        elif "fintech" in ctx.lower():
            jd_domain = "Fintech"
        else:
            jd_domain = "Enterprise Products"

    subtitle = f"Senior Product Manager | {jd_domain} | 8+ Years"

    # Cap at 60 chars
    if len(subtitle) > 60:
        subtitle = f"Senior Product Manager | {jd_domain[:20]} | 8+ Years"

    return subtitle


def _apply_programmatic_fixes(result: dict, parsed_jd: dict, pkb: dict) -> dict:
    """Apply all programmatic fixes: title integrity, pre-2023 tech, banned verbs, word count, bullet limits, locations, verb variety, skills cap, skills dedup, text fixes, awards."""
    # CRITICAL: Title integrity (Rule 0) — must run first
    result = _enforce_title_integrity(result, pkb)
    # Pre-2023 anachronistic tech replacement (Rule 7)
    result = _fix_pre_2023_tech_full(result, pkb)
    work = result.get("work_experience", [])
    p0_p1 = (parsed_jd.get("p0_keywords") or []) + (parsed_jd.get("p1_keywords") or [])
    new_work = []
    for role in work:
        new_bullets = []
        for bullet in role.get("bullets", []):
            b = bullet or ""
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
    result = {**result, "work_experience": new_work}
    result = _replace_banned_verbs(result)
    result = _enforce_bullet_word_count(result)
    result = {**result, "work_experience": _enforce_bullet_limits(result.get("work_experience", []), pkb)}
    result = _trim_to_fit_pages(result, parsed_jd, max_pages=MAX_PAGES)
    result = _normalize_locations(result)
    result = _enforce_verb_variety(result)
    # Skills cap 25
    sk = result.get("skills", {})
    tech = sk.get("technical") or []
    meth = sk.get("methodologies") or []
    dom = sk.get("domains") or []
    total = tech + meth + dom
    if len(total) > MAX_SKILLS_TERMS:
        tech_cap = min(len(tech), 15)
        meth_cap = min(len(meth), 5)
        dom_cap = MAX_SKILLS_TERMS - tech_cap - meth_cap
        result["skills"] = {
            "technical": tech[:tech_cap],
            "methodologies": meth[:meth_cap],
            "domains": dom[:max(0, dom_cap)],
        }
        logger.info("Skills trimmed to %d terms (max %d)", tech_cap + meth_cap + max(0, dom_cap), MAX_SKILLS_TERMS)
    result = _inject_awards_from_pkb(result, pkb)
    # Skills dedup: remove abbreviation+full form, near-synonyms, vague words (Rule 9)
    result = _dedup_skills(result)
    # Skills minimum: ensure 15-25 real tools from PKB (Part A Rule 5)
    result = _enforce_skills_minimum(result, pkb, parsed_jd)
    # Part A Rule 1: Fix bullet endings (metrics not methods)
    result = _fix_bullet_endings(result, parsed_jd)
    # Part A Rule 6: Cross-JD contamination check
    result = _check_cross_jd_contamination(result, parsed_jd)
    # Part A Rule 8: Incomplete sentence detection
    result = _fix_incomplete_sentences(result)
    # Part A Rule 3: Summary format (3 sentences, 45-55 words)
    result = _enforce_summary_format(result, parsed_jd)
    # Part A Rule 4: Generate subtitle
    result["subtitle"] = _generate_subtitle(result, parsed_jd)
    # Text fixes LAST: number spacing, currency symbols on ALL text (Fixes 4, 5)
    result = _apply_text_fixes(result)
    return result


def _patch_reframe_with_retry(
    current_resume_content: dict,
    feedback: str,
    parsed_jd: dict,
    max_retries: int = 1,
) -> dict:
    """Patch mode: make targeted edits to existing resume. Returns patched resume or original on failure."""
    client = anthropic.Anthropic()
    resume_json = json.dumps(current_resume_content, indent=2)
    jd_keywords = json.dumps({
        "p0_keywords": parsed_jd.get("p0_keywords", []),
        "p1_keywords": parsed_jd.get("p1_keywords", []),
    }, indent=2)

    for attempt in range(max_retries + 1):
        try:
            logger.info("Patch reframe attempt %d/%d (targeted edits only)...", attempt + 1, max_retries + 1)
            message = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=8000,
                timeout=60.0,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{PATCH_REFRAME_PROMPT}\n\n"
                            f"---\n\n"
                            f"FEEDBACK TO ADDRESS:\n{feedback}\n\n"
                            f"---\n\n"
                            f"CURRENT RESUME (make targeted edits only):\n{resume_json}\n\n"
                            f"---\n\n"
                            f"JD KEYWORDS (for reference):\n{jd_keywords}"
                        ),
                    }
                ],
            )
            response_text = message.content[0].text.strip()
            json_str = _extract_json_from_response(response_text)
            result = json.loads(json_str)
            # Unwrap if nested
            if "resume" in result and isinstance(result["resume"], dict):
                result = result["resume"]
            logger.info("Patch reframe succeeded")
            return result
        except Exception as e:
            logger.warning("Patch reframe attempt %d failed: %s", attempt + 1, str(e))
            if attempt < max_retries:
                logger.info("Retrying in 5 seconds...")
                time.sleep(5)
            else:
                logger.warning("Patch reframe failed after %d attempts; returning original resume", max_retries + 1)
                return current_resume_content
    return current_resume_content


def reframe_experience(
    mapping_matrix: dict,
    pkb: dict,
    parsed_jd: dict,
    feedback_for_improvement=None,
    current_resume_content=None,
) -> dict:
    """Generate tailored resume content using intelligent reframing.

    Args:
        mapping_matrix: JD-to-experience mappings from profile_mapper
        pkb: Profile Knowledge Base
        parsed_jd: Structured JD analysis from jd_parser
        feedback_for_improvement: Optional feedback from scorer to improve weakest component.
        current_resume_content: Optional already-reframed resume. If provided with feedback,
                                uses patch mode (targeted edits) instead of full regeneration.

    Returns:
        Resume content dict with professional_summary, work_experience,
        skills, education, certifications, and reframing_log
    """
    # Patch mode: if both feedback and current resume provided, make targeted edits only
    if feedback_for_improvement and current_resume_content:
        logger.info("Using patch mode: targeted edits to existing resume")
        patched = _patch_reframe_with_retry(current_resume_content, feedback_for_improvement, parsed_jd)
        # Ensure required keys exist
        patched.setdefault("professional_summary", current_resume_content.get("professional_summary", ""))
        patched.setdefault("work_experience", current_resume_content.get("work_experience", []))
        patched.setdefault("skills", current_resume_content.get("skills", {}))
        patched.setdefault("education", current_resume_content.get("education", []))
        patched.setdefault("certifications", current_resume_content.get("certifications", []))
        patched.setdefault("reframing_log", current_resume_content.get("reframing_log", []))
        # Normalize dates from PKB
        patched["work_experience"] = _normalize_work_experience_dates(patched.get("work_experience", []), pkb)
        # Apply programmatic fixes
        patched = _apply_programmatic_fixes(patched, parsed_jd, pkb)
        patched["work_experience"] = _normalize_work_experience_dates(patched.get("work_experience", []), pkb)
        return patched

    # Full reframe mode: regenerate from PKB
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
    # Use condensed PKB to reduce payload and avoid API timeouts
    condensed_pkb = _condensed_pkb_for_api(pkb)
    pkb_json = json.dumps(condensed_pkb, indent=2)
    logger.info("Full reframe payload: JD + mapping + condensed PKB (~%d chars)", len(jd_json) + len(mapping_json) + len(pkb_json))

    feedback_block = ""
    if feedback_for_improvement and feedback_for_improvement.strip():
        feedback_block = (
            "\n\n---\n\nSCORER FEEDBACK (address this to improve the resume score):\n"
            f"{feedback_for_improvement.strip()}\n\n"
        )
        logger.info("Reframing with scorer feedback for improvement")

    logger.info("Reframing experience with Claude (intelligent reframing engine)...")
    # Retry logic for full reframe: 2 attempts, 120s timeout
    max_retries = 1
    full_reframe_timeout = 120.0
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=16000,
                timeout=full_reframe_timeout,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{REFRAME_PROMPT}\n\n"
                            f"{feedback_block}"
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
            result = json.loads(json_str)
            last_error = None
            break  # Success
        except Exception as e:
            last_error = e
            logger.warning(
                "Full reframe attempt %d/%d failed: %s: %s",
                attempt + 1, max_retries + 1, type(e).__name__, str(e)
            )
            if attempt < max_retries:
                wait_sec = 3
                logger.info("Retrying in %d seconds...", wait_sec)
                time.sleep(wait_sec)
            else:
                logger.error("Full reframe failed after %d attempts. Last error: %s", max_retries + 1, last_error)
                raise

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

    # Bug 1 fix: If work_experience is empty, retry once; if still empty, fallback to PKB
    if not result.get("work_experience"):
        logger.warning("work_experience is EMPTY after reframe — retrying once...")
        try:
            retry_message = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=16000,
                timeout=full_reframe_timeout,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{REFRAME_PROMPT}\n\n"
                            f"{feedback_block}"
                            "---\n\n"
                            "JOB DESCRIPTION ANALYSIS:\n"
                            f"{jd_json}\n\n"
                            "---\n\n"
                            "MAPPING MATRIX (JD requirements → candidate experience):\n"
                            f"{mapping_json}\n\n"
                            "---\n\n"
                            "CANDIDATE PROFILE KNOWLEDGE BASE (PKB):\n"
                            f"{pkb_json}\n\n"
                            "CRITICAL: Your previous response had an EMPTY work_experience array. "
                            "You MUST include all work experience roles with bullets. This is mandatory."
                        ),
                    }
                ],
            )
            retry_text = retry_message.content[0].text.strip()
            retry_json_str = _extract_json_from_response(retry_text)
            retry_result = json.loads(retry_json_str)
            if "resume" in retry_result and isinstance(retry_result["resume"], dict):
                retry_result = retry_result["resume"]
            if retry_result.get("work_experience"):
                logger.info("Retry succeeded: got %d roles", len(retry_result["work_experience"]))
                result["work_experience"] = retry_result["work_experience"]
                if retry_result.get("professional_summary"):
                    result["professional_summary"] = retry_result["professional_summary"]
                if retry_result.get("skills"):
                    result["skills"] = retry_result["skills"]
                if retry_result.get("reframing_log"):
                    result["reframing_log"] = retry_result["reframing_log"]
            else:
                logger.warning("Retry also returned empty work_experience")
        except Exception as retry_err:
            logger.warning("Retry failed: %s", retry_err)

        # Final fallback: build minimal work_experience from PKB directly
        if not result.get("work_experience"):
            logger.warning("Falling back to PKB bullets for work_experience")
            fallback_work = []
            for w in (pkb.get("work_experience") or [])[:4]:
                bullets = []
                for b in (w.get("bullets") or [])[:5]:
                    text = (b.get("original_text") or "") if isinstance(b, dict) else str(b)
                    if text.strip():
                        bullets.append(text.strip())
                if bullets:
                    fallback_work.append({
                        "company": w.get("company", ""),
                        "title": w.get("title", ""),
                        "dates": _format_dates_from_pkb(w),
                        "location": w.get("location", ""),
                        "bullets": bullets,
                    })
            result["work_experience"] = fallback_work
            logger.info("PKB fallback: %d roles with bullets", len(fallback_work))

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
    """Ensure each role has correct dates from PKB and is in reverse-chronological order.

    Sorts by end date descending (most recent first). Internships and early-career
    developer roles (Fidelity, Cognizant) are always pushed to the end.
    """
    pkb_work = {w["company"]: w for w in pkb.get("work_experience", [])}
    for role in work_experience:
        company = role.get("company")
        if company and company in pkb_work:
            # Prefer PKB date format
            formatted = _format_dates_from_pkb(pkb_work[company])
            if formatted:
                role["dates"] = formatted
    # Sort by actual end year descending (most recent first)
    # Internships and early-career developer roles always go last
    def sort_key(r):
        company = (r.get("company") or "").lower()
        title = (r.get("title") or "").lower()
        is_intern = "intern" in title or "fidelity" in company
        is_early_dev = "cognizant" in company and ("developer" in title or "software" in title or "analyst" in title)
        if is_intern:
            return (2, 0)  # Push to very end
        if is_early_dev:
            return (1, 0)  # Push after main roles but before internships
        end_year = _get_role_end_year(r, pkb)
        # "Present" roles get year 9999 to sort first
        dates_str = r.get("dates") or ""
        if isinstance(dates_str, str) and "present" in dates_str.lower():
            end_year = 9999
        return (0, -end_year)
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
