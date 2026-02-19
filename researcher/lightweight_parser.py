"""
Lightweight JD parser for search results scoring.

Extracts keywords and signals using regex only (no LLM calls).
Produces a parsed_jd-compatible dict that score_job() can consume,
plus manual-review signal strings for UI display.

~5ms per JD — fast enough for 50+ results in a single request.
"""

import re

from researcher.job_scorer import (
    DOMAIN_TAXONOMY,
    SKILL_ALIASES,
    _REVERSE_ALIASES,
)

# Build a master set of all known skill terms (canonical + aliases)
_ALL_SKILL_TERMS = set()
for canonical, aliases in SKILL_ALIASES.items():
    _ALL_SKILL_TERMS.add(canonical.lower())
    for alias in aliases:
        _ALL_SKILL_TERMS.add(alias.lower())

# Pre-compile regex patterns for each skill term (word-boundary matching)
_SKILL_PATTERNS = {}
for term in _ALL_SKILL_TERMS:
    # Escape special regex chars in skill names (e.g., "a/b testing", "ci/cd")
    escaped = re.escape(term)
    _SKILL_PATTERNS[term] = re.compile(r'\b' + escaped + r'\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Compensation signal patterns
# ---------------------------------------------------------------------------
_COMP_PATTERNS = [
    re.compile(r'\$\s*[\d,]+(?:\s*[kK])?\s*[-–]\s*\$?\s*[\d,]+(?:\s*[kK])?', re.IGNORECASE),
    re.compile(r'₹\s*[\d,.]+\s*(?:LPA|lakh|lakhs|CTC|per\s+annum)', re.IGNORECASE),
    re.compile(r'(?:salary|compensation|CTC|pay)\s*(?:range)?[:\s]+[\d$₹€£,.\skKLPA–-]+', re.IGNORECASE),
    re.compile(r'\b(?:equity|stock\s+options?|RSU|ESOP|vesting)\b', re.IGNORECASE),
    re.compile(r'\b(?:bonus|signing\s+bonus|performance\s+bonus|annual\s+bonus)\b', re.IGNORECASE),
    re.compile(r'\b(?:health\s+insurance|medical\s+(?:insurance|benefits)|dental|vision|401k|PF|provident\s+fund)\b', re.IGNORECASE),
    re.compile(r'\b(?:unlimited\s+PTO|paid\s+time\s+off|vacation\s+days?)\b', re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Culture signal patterns
# ---------------------------------------------------------------------------
_CULTURE_KEYWORDS = [
    "remote-first", "remote first", "fully remote", "work from home",
    "hybrid", "flexible hours", "flexible schedule", "flex time",
    "async", "asynchronous", "work-life balance", "work life balance",
    "collaborative", "fast-paced", "fast paced", "startup culture",
    "flat hierarchy", "no bureaucracy", "autonomous", "self-directed",
    "inclusive", "diversity", "DEI", "employee wellness",
    "learning budget", "education budget", "professional development",
    "team offsites", "company retreats",
]

# ---------------------------------------------------------------------------
# International presence patterns
# ---------------------------------------------------------------------------
_INTERNATIONAL_PATTERNS = [
    re.compile(r'\b(?:global|international|worldwide|multinational)\b', re.IGNORECASE),
    re.compile(r'\boffices?\s+(?:in|across)\s+\d+\s+(?:countries|locations|cities)', re.IGNORECASE),
    re.compile(r'\b(?:distributed\s+team|global\s+team|international\s+team)\b', re.IGNORECASE),
    re.compile(r'\bpresence\s+(?:in|across)\b.*(?:US|Europe|Asia|EMEA|APAC)', re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Company performance patterns
# ---------------------------------------------------------------------------
_COMPANY_PATTERNS = [
    re.compile(r'\bSeries\s+[A-F]\b', re.IGNORECASE),
    re.compile(r'\b(?:IPO|publicly\s+traded|NYSE|NASDAQ|BSE|NSE)\b', re.IGNORECASE),
    re.compile(r'\b(?:unicorn|decacorn)\b', re.IGNORECASE),
    re.compile(r'\b(?:raised|funding|funded)\s+\$?[\d,.]+\s*[MBmb](?:illion)?\b', re.IGNORECASE),
    re.compile(r'\b(?:revenue|ARR)\s+(?:of\s+)?\$?[\d,.]+\s*[MBmb](?:illion)?\b', re.IGNORECASE),
    re.compile(r'\b(?:growing|growth)\s+\d+%', re.IGNORECASE),
    re.compile(r'\b(?:Fortune\s+\d+|Inc\.\s*\d+|Forbes)\b', re.IGNORECASE),
    re.compile(r'\b\d+[,+]?\s*employees?\b', re.IGNORECASE),
    re.compile(r'\b(?:profitable|profitability|cash-flow\s+positive)\b', re.IGNORECASE),
]


def lightweight_parse_jd(
    description: str,
    title: str = "",
    company: str = "",
    location: str = "",
) -> dict:
    """Parse a job description using regex only (no LLM).

    Returns a dict compatible with job_scorer.score_job() plus
    manual-review signal strings for UI display.
    """
    desc_lower = description.lower() if description else ""
    title_lower = title.lower() if title else ""

    # --- Extract skills (P0 / P1 classification) ---
    skill_counts = {}  # canonical_skill -> count in description
    title_skills = set()  # skills found in title

    # Track which canonical skills we've already counted via aliases
    # to prevent double-counting synonyms (e.g. "analytics" matching both
    # "product analytics" and "data analytics" aliases)
    canonical_counted = set()

    for term, pattern in _SKILL_PATTERNS.items():
        canonical = _REVERSE_ALIASES.get(term, term)

        # Check title
        if title and pattern.search(title):
            title_skills.add(canonical)

        # Count in description — only count the first alias match per canonical skill
        if description and canonical not in canonical_counted:
            matches = pattern.findall(description)
            if matches:
                # Set count once per canonical (take the max across aliases)
                prev = skill_counts.get(canonical, 0)
                skill_counts[canonical] = max(prev, len(matches))
                canonical_counted.add(canonical)

    # Classify: P0 = in title OR count >= 2; P1 = count == 1
    p0_keywords = set()
    p1_keywords = set()

    for skill in title_skills:
        p0_keywords.add(skill)

    for skill, count in skill_counts.items():
        if skill in title_skills or count >= 2:
            p0_keywords.add(skill)
        elif count == 1:
            p1_keywords.add(skill)

    # Remove from P1 anything already in P0
    p1_keywords -= p0_keywords

    # --- Extract industry terms ---
    industry_terms = []
    for category, signals in DOMAIN_TAXONOMY.items():
        for signal in signals:
            if signal.lower() in desc_lower or signal.lower() in title_lower:
                industry_terms.append({"term": signal, "category": category})

    # --- Extract manual-review signals ---
    compensation = _extract_signals(description, _COMP_PATTERNS)
    culture = _extract_culture_signals(desc_lower)
    international = _extract_signals(description, _INTERNATIONAL_PATTERNS)
    company_health = _extract_signals(description, _COMPANY_PATTERNS)

    return {
        # Fields consumed by score_job()
        "job_title": title,
        "company": company,
        "location": location,
        "company_context": company,
        "industry_terms": industry_terms,
        "p0_keywords": sorted(p0_keywords),
        "p1_keywords": sorted(p1_keywords),
        "p2_keywords": [],
        "job_level": _infer_job_level(title),
        # For domain detection (full-text scan)
        "_description_lower": desc_lower,
        # Manual-review signals (not scored, displayed in UI)
        "signals": {
            "compensation": compensation or "Not mentioned",
            "culture": culture or "Not mentioned",
            "international": international or "Not mentioned",
            "company_health": company_health or "Not mentioned",
        },
    }


def _extract_signals(text: str, patterns: list) -> str:
    """Extract all matching signal strings from text, deduplicated."""
    if not text:
        return ""
    found = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            snippet = match.group(0).strip()
            if snippet and snippet not in found:
                found.append(snippet)
    return " | ".join(found[:4])  # Cap at 4 signals


def _extract_culture_signals(desc_lower: str) -> str:
    """Extract culture keywords found in description."""
    if not desc_lower:
        return ""
    found = []
    for keyword in _CULTURE_KEYWORDS:
        if keyword.lower() in desc_lower and keyword not in found:
            found.append(keyword)
    return " | ".join(found[:4])


def _infer_job_level(title: str) -> str:
    """Infer seniority level from title string."""
    t = title.lower() if title else ""
    if any(w in t for w in ["vp", "vice president", "cpo", "chief", "head of"]):
        return "VP+"
    if any(w in t for w in ["director", "senior director"]):
        return "Director"
    if any(w in t for w in ["principal", "lead", "staff", "group"]):
        return "Principal/Lead"
    if any(w in t for w in ["senior", "sr.", "sr "]):
        return "Senior IC"
    if any(w in t for w in ["associate", "junior", "entry", "apm"]):
        return "Junior"
    return "IC"


# ---------------------------------------------------------------------------
# Custom location scoring for search UI (20 pts max — reduced from 35)
# ---------------------------------------------------------------------------

_REMOTE_SIGNALS = ["remote", "work from home", "wfh", "anywhere", "distributed"]
_PREFERRED_CITIES = ["bangalore", "bengaluru", "hyderabad"]
_PREMIUM_REGIONS = [
    # US
    "united states", "usa", "us,", " us ", "new york", "san francisco",
    "seattle", "austin", "boston", "chicago", "los angeles",
    # UK
    "united kingdom", "uk,", " uk ", "london", "manchester",
    # Europe
    "europe", "germany", "berlin", "amsterdam", "paris", "dublin",
    "stockholm", "zurich", "barcelona",
    # Dubai / UAE
    "dubai", "uae", "abu dhabi", "qatar", "bahrain",
]
_INDIA_SIGNALS = ["india", "mumbai", "pune", "delhi", "gurgaon", "gurugram",
                  "noida", "chennai", "kolkata", "jaipur", "kochi",
                  "ahmedabad", "chandigarh"]


def score_location(location: str) -> dict:
    """Score location fit (max 20 points).

    Remote = 20, Preferred city = 20, Other India = 15,
    Dubai/ME = 14, US/UK/Europe = 10, Unknown = 5, Other = 6
    """
    loc = location.lower() if location else ""

    if not loc:
        return {"score": 5, "max": 20, "reason": "Unknown location"}

    # Remote — top tier
    if any(s in loc for s in _REMOTE_SIGNALS):
        return {"score": 20, "max": 20, "reason": "Remote"}

    # Preferred cities (Bangalore, Hyderabad)
    if any(city in loc for city in _PREFERRED_CITIES):
        return {"score": 20, "max": 20, "reason": "Preferred city"}

    # Other India cities
    if any(s in loc for s in _INDIA_SIGNALS):
        return {"score": 15, "max": 20, "reason": "Other India"}

    # Dubai/ME — relocation-friendly target geography
    _ME_SIGNALS = ["dubai", "uae", "abu dhabi", "qatar", "bahrain"]
    if any(s in loc for s in _ME_SIGNALS):
        return {"score": 14, "max": 20, "reason": "Target geography (Middle East)"}

    # US/UK/Europe — require relocation/visa, score lower
    if any(region in loc for region in _PREMIUM_REGIONS):
        return {"score": 10, "max": 20, "reason": "Premium international (relocation needed)"}

    # Other international
    return {"score": 6, "max": 20, "reason": f"Other: {location}"}


# ---------------------------------------------------------------------------
# Custom combined scoring for search UI (100 pts max)
# ---------------------------------------------------------------------------

def score_search_result(
    job: dict,
    parsed_jd: dict,
    pkb: dict,
    candidate_skills: set,
    candidate_domains: set,
) -> dict:
    """Score a search result using custom formula: Location (20) + Profile Fit (80).

    Profile Fit (80) is composed of:
      - Domain Match: 23 pts (scaled from score_domain_match max 30)
      - Keyword Overlap: 27 pts (scaled from score_keyword_overlap max 25)
      - Title Match: 20 pts (scaled from score_title_match max 20)
      - Recency: 10 pts (same as score_recency max 10)

    Returns scoring dict with fit_score, recommendation, components, missing skills.
    """
    from researcher.job_scorer import (
        score_domain_match,
        score_keyword_overlap,
        score_title_match,
        score_recency,
        experience_compatibility,
    )

    # Location (20 pts) — reduced from 35 so profile fit dominates
    location_result = score_location(job.get("location", ""))

    # Profile fit components — use existing scorers, then rescale to 80 pts total
    domain_raw = score_domain_match(parsed_jd, candidate_domains)
    keyword_raw = score_keyword_overlap(parsed_jd, candidate_skills)
    title_raw = score_title_match(parsed_jd, pkb)
    recency_raw = score_recency(job.get("posted_days_ago"))

    # Scale to new maxes: domain 30→23, keyword 25→27, title 20→20, recency 10→10
    domain_scaled = round(domain_raw["score"] * (23 / 30), 1)
    keyword_scaled = round(keyword_raw["score"] * (27 / 25), 1)
    title_scaled = round(title_raw["score"] * (20 / 20), 1)
    recency_scaled = recency_raw["score"]  # already max 10

    # Domain priority: fintech/SaaS top; other domains score ~8% lower
    PREFERRED_DOMAINS = {"fintech", "enterprise_saas"}
    overlap = domain_raw.get("matched_domains", [])
    if overlap and not any(d in PREFERRED_DOMAINS for d in overlap):
        domain_scaled = round(domain_scaled * 0.92, 1)

    profile_fit = round(domain_scaled + keyword_scaled + title_scaled + recency_scaled, 1)
    total = round(location_result["score"] + profile_fit, 1)

    # Experience compatibility multiplier
    jd_text = job.get("description", "")
    exp_result = {"multiplier": 1.0, "required_years": None, "gap": 0, "note": "No JD text"}
    if jd_text:
        exp_result = experience_compatibility(jd_text, parsed_jd)
        total = round(total * exp_result["multiplier"], 1)

    # Preferred domain boost: fintech/SaaS jobs rank higher when candidate matches
    if overlap and any(d in PREFERRED_DOMAINS for d in overlap):
        total = round(total + 4, 1)  # +4 pts for fintech/SaaS match (reduced from +8)

    # Tier assignment
    if total >= 85:
        recommendation = "APPLY TODAY"
    elif total >= 70:
        recommendation = "WORTH TRYING"
    elif total >= 55:
        recommendation = "STRETCH"
    else:
        recommendation = "SKIP"

    # No PM role override — let low-scoring PM roles stay as SKIP.
    # Title filter in research.py already ensures only Senior PM+ roles reach here.

    components = {
        "location_fit": {**location_result},
        "profile_fit": {
            "score": profile_fit,
            "max": 80,
            "domain_match": {"score": domain_scaled, "max": 23, "raw": domain_raw},
            "keyword_overlap": {"score": keyword_scaled, "max": 27, "raw": keyword_raw},
            "title_match": {"score": title_scaled, "max": 20, "raw": title_raw},
            "recency": {"score": recency_scaled, "max": 10, "raw": recency_raw},
        },
        "experience_compatibility": exp_result,
    }

    return {
        "fit_score": total,
        "recommendation": recommendation,
        "components": components,
        "missing_critical_skills": keyword_raw.get("missing_p0", []),
    }
