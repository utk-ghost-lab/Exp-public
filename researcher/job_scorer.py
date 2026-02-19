"""
Phase 2 Component 3: Job-Profile Fit Scoring Engine

Scores discovered jobs against user's PKB using 5 weighted components.
Domain depth is the primary signal — hiring managers want "done this exact thing before."

FIT_SCORE = Domain(30) + Keyword(25) + Title(20) + Location(15) + Recency(10)
"""

import json
import re
import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Domain taxonomy — maps broad domain categories to JD signal words
# ---------------------------------------------------------------------------

DOMAIN_TAXONOMY = {
    "enterprise_saas": [
        "SaaS", "B2B", "enterprise software", "platform", "FP&A", "ERP",
        "enterprise", "cloud platform", "multi-tenant", "subscription",
        "enterprise planning", "cloud-based", "self-service",
        "software as a service", "b2b saas",
    ],
    "fintech": [
        "fintech", "payments", "digital banking", "wealth", "insurance",
        "lending", "financial services", "banking", "neobank", "credit",
        "financial technology", "wealth management", "fixed deposits",
        "foreign exchange", "annuity", "ULIP", "insurtech",
    ],
    "investment_capital_markets": [
        "investment", "capital markets", "asset management", "trading",
        "investment services", "institutional", "securities", "hedge fund",
        "private equity", "venture capital", "portfolio management",
    ],
    "ai_ml_products": [
        "AI", "ML", "machine learning", "LLM", "predictive", "decisioning",
        "NLP", "deep learning", "artificial intelligence", "generative AI",
        "conversational AI", "agentic AI", "voice AI", "predictive analytics",
        "data science", "computer vision",
    ],
    "data_platform_analytics": [
        "data platform", "data products", "data analytics", "data analysis",
        "analytics platform", "business intelligence", "BI", "data warehouse",
        "ETL", "data pipeline", "metrics", "dashboards",
    ],
    "contact_center_crm": [
        "CRM", "contact center", "customer success", "agent", "omnichannel",
        "customer service", "helpdesk", "ticketing", "support platform",
        "customer engagement", "customer experience", "agent productivity",
        "messaging",
    ],
    "financial_planning": [
        "FP&A", "financial planning", "budgeting", "forecasting",
        "financial analysis", "planning and analysis", "corporate finance",
        "revenue planning", "expense management",
    ],
}

# Adjacent domains for partial credit when no direct overlap
_DOMAIN_ADJACENCIES = {
    "financial_planning": {"fintech", "investment_capital_markets"},
    "fintech": {"financial_planning", "investment_capital_markets"},
    "investment_capital_markets": {"fintech", "financial_planning"},
    "enterprise_saas": {"contact_center_crm", "data_platform_analytics"},
    "contact_center_crm": {"enterprise_saas"},
    "data_platform_analytics": {"enterprise_saas", "ai_ml_products"},
    "ai_ml_products": {"data_platform_analytics"},
}

# ---------------------------------------------------------------------------
# Career Direction Config — upward title targeting
# Scoring: current level = full score, one level up = full score,
# two levels up = reduced but surfaced, VP+ = skip, demotion = hard filter.
# ---------------------------------------------------------------------------

TITLE_LEVEL_MAP = {
    "senior_pm": {
        "titles": [
            "Senior Product Manager", "Senior PM", "Sr. Product Manager",
            "Sr PM", "Product Manager II", "PM II", "L5", "L5 PM",
            "Product Lead", "Senior Associate Product Manager"
        ],
        "score": 20,
        "label": "current_level"
    },
    "principal_pm": {
        "titles": [
            "Principal Product Manager", "Principal PM",
            "Lead Product Manager", "Lead PM", "Staff PM",
            "Group Product Manager", "GPM",
            "Product Manager III", "PM III", "L6", "L6 PM",
            "Senior Product Lead", "Product Management Lead",
            "Senior Staff PM", "Senior Group PM"
        ],
        "score": 20,   # intentional — one level up is the target
        "label": "target_level"
    },
    "associate_director": {
        "titles": [
            "Associate Director of Product", "Associate Director PM",
            "Associate Director Product Management",
            "Director of Product Management", "Director of Product",
            "Director PM", "Senior Director of Product",
            "Senior Director PM", "Group PM Director"
        ],
        "score": 15,   # reduced but surfaced — good stretch
        "label": "stretch_level"
    },
    "vp_above": {
        "titles": [
            "VP Product", "VP of Product", "Vice President Product",
            "CPO", "Chief Product Officer", "Head of Product",
            "SVP Product", "EVP Product"
        ],
        "score": 3,    # effectively filtered — too far a stretch
        "label": "too_senior"
    },
    "junior": {
        "titles": [
            "Associate PM", "Junior PM", "APM", "Junior Product Manager",
            "Product Analyst", "Product Coordinator", "Entry Level PM",
            "Assistant Product Manager"
        ],
        "score": 0,    # hard filter — never show demotion roles
        "label": "demotion"
    }
}


def title_match_score(jd_title: str, candidate_titles: list) -> dict:
    """
    Score title match with career direction awareness.
    Returns score (0-20) + metadata for shortlist output.

    Rules:
    - Current level (Senior PM) = 20 points
    - One level up (Principal/Lead/GPM) = 20 points (intentional target)
    - Two levels up (Director) = 15 points (stretch, still surfaced)
    - VP+ = 3 points (too far, effectively filtered)
    - Demotion (Junior/APM) = 0 points (hard filter)
    """
    jd_title_lower = jd_title.lower()

    for level_key, level_data in TITLE_LEVEL_MAP.items():
        for title in level_data["titles"]:
            if title.lower() in jd_title_lower or jd_title_lower in title.lower():
                return {
                    "score": level_data["score"],
                    "max": 20,
                    "matched_level": level_key,
                    "label": level_data["label"],
                    "jd_title": jd_title,
                    "match_type": "exact" if title.lower() == jd_title_lower else "contains"
                }

    # "Product Management" as department/function (e.g. "Lead Manager, Product Management")
    if "product management" in jd_title_lower:
        if any(w in jd_title_lower for w in ["junior", "associate", "entry", "assistant"]):
            return {
                "score": 0,
                "max": 20,
                "matched_level": "junior",
                "label": "demotion",
                "jd_title": jd_title,
                "match_type": "pm_function_demotion"
            }
        if any(w in jd_title_lower for w in ["lead", "principal", "director", "head", "staff", "group"]):
            return {
                "score": 20,
                "max": 20,
                "matched_level": "principal_pm",
                "label": "pm_function_senior",
                "jd_title": jd_title,
                "match_type": "pm_function_senior"
            }
        return {
            "score": 16,
            "max": 20,
            "matched_level": "generic_pm",
            "label": "pm_function_generic",
            "jd_title": jd_title,
            "match_type": "pm_function_generic"
        }

    # Generic "Product Manager" without seniority qualifier
    if "product manager" in jd_title_lower and not any(
        word in jd_title_lower for word in ["junior", "associate", "entry", "vp", "director", "head"]
    ):
        return {
            "score": 16,
            "max": 20,
            "matched_level": "generic_pm",
            "label": "generic_match",
            "jd_title": jd_title,
            "match_type": "generic"
        }

    return {
        "score": 5,
        "max": 20,
        "matched_level": "unrelated",
        "label": "no_match",
        "jd_title": jd_title,
        "match_type": "none"
    }

# ---------------------------------------------------------------------------
# Skill alias map — common PM skill synonyms for fuzzy matching
# ---------------------------------------------------------------------------

SKILL_ALIASES = {
    "a/b testing": ["ab testing", "split testing", "experimentation"],
    "sql": ["structured query language"],
    "product roadmap": ["roadmapping", "product roadmapping", "roadmap"],
    "agile": ["agile methodology", "agile development", "scrum"],
    "scrum": ["scrum methodology", "agile scrum"],
    "jira": ["atlassian jira"],
    "stakeholder management": ["stakeholder alignment", "stakeholder engagement"],
    "cross-functional": ["cross functional", "x-functional"],
    "data-driven": ["data driven", "data informed"],
    "user research": ["customer research", "user interviews", "customer discovery"],
    "okr": ["okrs", "objectives and key results"],
    "kpi": ["kpis", "key performance indicators"],
    "product analytics": ["product metrics", "analytics"],
    "figma": ["figma design"],
    "tableau": ["tableau analytics"],
    "amplitude": ["amplitude analytics"],
    "mixpanel": ["mixpanel analytics"],
    "design thinking": ["human-centered design"],
    "api": ["apis", "rest api", "restful api"],
    "saas": ["software as a service"],
    "b2b": ["business to business"],
    "b2c": ["business to consumer"],
    "crm": ["customer relationship management"],
    "erp": ["enterprise resource planning"],
    "fp&a": ["financial planning and analysis", "financial planning & analysis"],
    "llm": ["large language model", "large language models"],
    "nlp": ["natural language processing"],
    "ml": ["machine learning"],
    "ai": ["artificial intelligence"],
    "ci/cd": ["continuous integration", "continuous deployment"],
    "product-market fit": ["pmf", "product market fit"],
    "go-to-market": ["gtm", "go to market"],
    "customer journey": ["user journey", "customer lifecycle"],
    "retention": ["user retention", "customer retention"],
    "churn": ["churn reduction", "churn rate"],
    "conversion": ["conversion rate", "conversion optimization"],
    "onboarding": ["user onboarding", "customer onboarding"],
    "pricing": ["pricing strategy", "pricing model"],
    "revenue": ["revenue growth", "revenue optimization"],
    "growth": ["growth strategy", "product-led growth", "plg"],
    "marketplace": ["two-sided marketplace", "platform marketplace"],
    "fintech": ["financial technology"],
    "payments": ["payment processing", "payment platform"],
    "pos": ["point of sale"],
    # Additional PM terms for better coverage
    "product discovery": ["discovery", "product discovery process"],
    "prioritization": ["prioritisation", "prioritize", "prioritise"],
    "backlog": ["product backlog", "backlog management"],
    "user stories": ["user story", "stories"],
    "stakeholder": ["stakeholders"],
    "metrics": ["product metrics", "business metrics"],
    "analytics": ["product analytics", "data analytics"],
    "experimentation": ["experiments", "a/b testing"],
    "integrations": ["integration", "api integration"],
    "launch": ["product launch", "launches"],
    "roadmap": ["roadmapping", "product roadmap"],
}

# Build reverse alias map: alias → canonical form
_REVERSE_ALIASES = {}
for canonical, aliases in SKILL_ALIASES.items():
    _REVERSE_ALIASES[canonical.lower()] = canonical.lower()
    for alias in aliases:
        _REVERSE_ALIASES[alias.lower()] = canonical.lower()


def _normalize_skill(skill: str) -> str:
    """Normalize a skill string for comparison."""
    s = skill.lower().strip()
    return _REVERSE_ALIASES.get(s, s)


def _build_candidate_skills(pkb: dict) -> set:
    """Flatten all skills from PKB into a normalized set."""
    skills = set()
    for category in ["hard_skills", "soft_skills", "tools", "methodologies", "domains"]:
        for s in pkb.get("skills", {}).get(category, []):
            skills.add(_normalize_skill(s))
    for exp in pkb.get("work_experience", []):
        for bullet in exp.get("bullets", []):
            for s in bullet.get("skills_demonstrated", []):
                skills.add(_normalize_skill(s))
            for t in bullet.get("tools_used", []):
                skills.add(_normalize_skill(t))
    for kw in pkb.get("all_experience_keywords", []):
        skills.add(_normalize_skill(kw))
    return skills


def _load_experience_config() -> dict:
    """Load experience config from job_criteria.json."""
    path = Path(__file__).parent.parent / "data" / "job_criteria.json"
    try:
        with open(path) as f:
            return json.load(f).get("experience", {})
    except Exception:
        return {}


CANDIDATE_EXPERIENCE_YEARS = _load_experience_config().get("actual_years", 9)


def experience_compatibility(jd_text: str, jd_parsed: dict) -> dict:
    """
    Extract required years from JD and compute compatibility score.
    Never hard-filter — always return a score. Experience requirements
    are aspirational; companies routinely hire 1-2 years below stated floor.

    Returns: {"multiplier": 0.0-1.0, "required_years": int, "gap": int, "note": str}
    """
    # Extract years from JD text — handle multiple patterns
    patterns = [
        r'(\d+)\+?\s*years?\s+of\s+(?:relevant\s+)?(?:product\s+management|pm|product)',
        r'minimum\s+(\d+)\s+years?',
        r'(\d+)\s+years?\s+minimum',
        r'(\d+)[-–](\d+)\s+years?\s+(?:of\s+)?experience',
        r'(\d+)\+\s+years?\s+experience',
        r'(\d+)\s+or\s+more\s+years?',
        r'(\d+)\+?\s*years?\s+(?:of\s+)?experience',
    ]

    required_min = None
    required_max = None

    search_text = (jd_text or "").lower()
    for pattern in patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            groups = match.groups()
            required_min = int(groups[0])
            if len(groups) > 1 and groups[1]:
                required_max = int(groups[1])
            break

    if required_min is None:
        # No experience requirement found — full compatibility
        return {"multiplier": 1.0, "required_years": None, "gap": 0, "note": "No years requirement stated"}

    gap = required_min - CANDIDATE_EXPERIENCE_YEARS

    if gap <= 0:
        return {"multiplier": 1.0, "required_years": required_min, "gap": 0, "note": "Meets requirement"}
    elif gap == 1:
        return {"multiplier": 0.97, "required_years": required_min, "gap": 1, "note": "1 year below — apply, minor gap"}
    elif gap == 2:
        return {"multiplier": 0.90, "required_years": required_min, "gap": 2, "note": "2 years below — apply, requirement likely padded"}
    elif gap == 3:
        return {"multiplier": 0.70, "required_years": required_min, "gap": 3, "note": "3 years below — surface but flag"}
    else:
        return {"multiplier": 0.40, "required_years": required_min, "gap": gap, "note": f"{gap} years below — genuine gap, low priority"}


def _build_candidate_domains(pkb: dict) -> set:
    """Determine which domain categories the candidate belongs to."""
    domains = set()
    pkb_domains = [d.lower() for d in pkb.get("skills", {}).get("domains", [])]
    pkb_industries = []
    for exp in pkb.get("work_experience", []):
        ind = exp.get("industry", "")
        if ind:
            pkb_industries.extend([i.strip().lower() for i in ind.split(",")])

    all_domain_text = pkb_domains + pkb_industries

    for category, signals in DOMAIN_TAXONOMY.items():
        for signal in signals:
            if signal.lower() in all_domain_text or any(signal.lower() in d for d in all_domain_text):
                domains.add(category)
                break
    return domains


# ===================================================================
# SCORING COMPONENTS
# ===================================================================

def score_domain_match(parsed_jd: dict, candidate_domains: set) -> dict:
    """Score domain overlap between JD and candidate (max 30 points).

    - Uses industry_terms, company_context, job_title, and optionally description for JD domain detection.
    - Partial credit (6 pts): when jd_domains exist but no overlap, give 6 pts if adjacent domain match.
    """
    jd_signals = []
    for term_obj in parsed_jd.get("industry_terms", []):
        t = term_obj.get("term", "") if isinstance(term_obj, dict) else str(term_obj)
        jd_signals.append(t.lower())
    ctx = parsed_jd.get("company_context", "")
    if ctx:
        jd_signals.append(ctx.lower())
    title = parsed_jd.get("job_title", "").lower()
    jd_signals.append(title)
    # Use full description for domain detection if available (improves coverage)
    desc = parsed_jd.get("_description_lower", "")
    if desc:
        jd_signals.append(desc)

    jd_domains = set()
    for category, signals in DOMAIN_TAXONOMY.items():
        for signal in signals:
            if any(signal.lower() in s for s in jd_signals):
                jd_domains.add(category)
                break

    if not jd_domains:
        return {"score": 5, "max": 30, "matched_domains": [], "reason": "no_domain_detected"}

    overlap = candidate_domains & jd_domains
    if overlap:
        ratio = len(overlap) / len(jd_domains)
        score = round(21 + (9 * ratio))  # 21-30 for exact match
        return {"score": min(score, 30), "max": 30, "matched_domains": sorted(overlap)}
    else:
        # Partial credit: adjacent domain match (e.g. financial_planning vs fintech)
        for jd_d in jd_domains:
            adj = _DOMAIN_ADJACENCIES.get(jd_d, set())
            if adj & candidate_domains:
                return {"score": 6, "max": 30, "matched_domains": [], "reason": "adjacent_domain"}
        return {"score": 0, "max": 30, "matched_domains": [], "reason": "no_domain_overlap"}


def score_keyword_overlap(parsed_jd: dict, candidate_skills: set) -> dict:
    """Score keyword overlap in fast mode (max 25 points).

    - Fallback when P0 empty: use P1 rate for full score if P1 has matches.
    - Soften P0 penalty: when P0_total > 0 but P0_matched = 0, give 3 pts base if P1_match > 0.
    """
    p0 = [_normalize_skill(k) for k in parsed_jd.get("p0_keywords", [])]
    p1 = [_normalize_skill(k) for k in parsed_jd.get("p1_keywords", [])]

    p0_matched = [k for k in p0 if k in candidate_skills]
    p1_matched = [k for k in p1 if k in candidate_skills]

    p0_rate = len(p0_matched) / max(len(p0), 1)
    p1_rate = len(p1_matched) / max(len(p1), 1)

    if len(p0) == 0 and len(p1) > 0:
        # Fallback: no P0 extracted, use P1 rate for full score
        score = round(p1_rate * 25, 1)
    elif len(p0) > 0 and len(p0_matched) == 0 and len(p1_matched) > 0:
        # Soften P0 penalty: strong P1 overlap gets 3 pts base
        base = round((p0_rate * 0.70 + p1_rate * 0.30) * 25, 1)
        score = max(3.0, base)
    else:
        score = round((p0_rate * 0.70 + p1_rate * 0.30) * 25, 1)

    missing_p0 = [k for k in p0 if k not in candidate_skills]

    return {
        "score": min(score, 25),
        "max": 25,
        "p0_matched": len(p0_matched),
        "p0_total": len(p0),
        "p1_matched": len(p1_matched),
        "p1_total": len(p1),
        "missing_p0": missing_p0[:5],  # Top 5 missing
    }


def score_title_match(parsed_jd: dict, pkb: dict) -> dict:
    """Score title alignment (max 20 points) with career direction awareness."""
    jd_title = parsed_jd.get("job_title", "")
    candidate_titles = [exp.get("title", "") for exp in pkb.get("work_experience", [])]
    return title_match_score(jd_title, candidate_titles)


def score_location_fit(parsed_jd: dict, pkb: dict) -> dict:
    """Score location compatibility (max 15 points)."""
    jd_location = parsed_jd.get("location", "").lower()
    user_location = pkb.get("personal_info", {}).get("location", "").lower()
    user_city = user_location.split(",")[0].strip() if user_location else ""
    user_country = "india"  # Default from PKB

    # Remote detection
    remote_signals = ["remote", "work from home", "wfh", "anywhere", "distributed"]
    is_remote = any(s in jd_location for s in remote_signals)

    if is_remote:
        return {"score": 15, "max": 15, "reason": "Remote"}

    if user_city and user_city in jd_location:
        return {"score": 15, "max": 15, "reason": f"City match: {user_city}"}

    if user_country in jd_location:
        return {"score": 10, "max": 15, "reason": "Same country (India)"}

    # Middle East target geography
    me_signals = ["dubai", "uae", "abu dhabi", "saudi", "bahrain", "qatar", "middle east"]
    if any(s in jd_location for s in me_signals):
        return {"score": 10, "max": 15, "reason": "Target geography (Middle East)"}

    # Europe
    eu_signals = ["london", "uk", "germany", "berlin", "amsterdam", "europe", "eu"]
    if any(s in jd_location for s in eu_signals):
        return {"score": 5, "max": 15, "reason": "Europe"}

    return {"score": 3, "max": 15, "reason": f"Other location: {jd_location}"}


def score_recency(posted_days_ago: int) -> dict:
    """Score job posting freshness (max 10 points)."""
    if posted_days_ago is None or posted_days_ago < 0:
        score = 5  # Unknown posting date
        reason = "Unknown posting date"
    elif posted_days_ago == 0:
        score = 10
        reason = "Posted today"
    elif posted_days_ago <= 2:
        score = 9
        reason = f"Very fresh ({posted_days_ago}d ago)"
    elif posted_days_ago <= 7:
        score = 7
        reason = f"This week ({posted_days_ago}d ago)"
    elif posted_days_ago <= 14:
        score = 4.5
        reason = f"Two weeks ({posted_days_ago}d ago)"
    elif posted_days_ago <= 30:
        score = 2
        reason = f"This month ({posted_days_ago}d ago)"
    else:
        score = 0.5
        reason = f"Stale ({posted_days_ago}d ago)"

    return {"score": score, "max": 10, "posted_days_ago": posted_days_ago, "reason": reason}


# ===================================================================
# MAIN SCORER
# ===================================================================

def score_job(parsed_jd: dict, pkb: dict, posted_days_ago: int = None,
              candidate_skills: set = None, candidate_domains: set = None,
              jd_text: str = None) -> dict:
    """Score a single job against user's profile.

    Args:
        parsed_jd: Output from engine/jd_parser.parse_jd()
        pkb: Full PKB dict (data/pkb.json)
        posted_days_ago: Days since job was posted (None = unknown)
        candidate_skills: Pre-computed skill set (optimization for batch scoring)
        candidate_domains: Pre-computed domain set (optimization for batch scoring)
        jd_text: Full JD text for experience requirement extraction (optional)

    Returns:
        Scoring dict with fit_score, recommendation, and component breakdown.
    """
    if candidate_skills is None:
        candidate_skills = _build_candidate_skills(pkb)
    if candidate_domains is None:
        candidate_domains = _build_candidate_domains(pkb)

    domain = score_domain_match(parsed_jd, candidate_domains)
    keyword = score_keyword_overlap(parsed_jd, candidate_skills)
    title = score_title_match(parsed_jd, pkb)
    location = score_location_fit(parsed_jd, pkb)
    recency = score_recency(posted_days_ago)

    total = round(domain["score"] + keyword["score"] + title["score"] +
                  location["score"] + recency["score"], 1)

    # Apply experience compatibility multiplier when jd_text is provided
    experience_result = {"multiplier": 1.0, "required_years": None, "gap": 0, "note": "No JD text provided"}
    if jd_text:
        experience_result = experience_compatibility(jd_text, parsed_jd)
        total = round(total * experience_result["multiplier"], 1)

    if total >= 85:
        recommendation = "APPLY TODAY"
    elif total >= 70:
        recommendation = "WORTH TRYING"
    elif total >= 55:
        recommendation = "STRETCH"
    else:
        recommendation = "SKIP"

    # Collect critical missing skills (missing P0 keywords)
    missing = keyword.get("missing_p0", [])

    components = {
        "domain_match": domain,
        "keyword_overlap": keyword,
        "title_match": title,
        "location_fit": location,
        "recency": recency,
        "experience_compatibility": experience_result,
    }
    return {
        "fit_score": total,
        "recommendation": recommendation,
        "components": components,
        "missing_critical_skills": missing,
    }


def score_jobs_batch(jobs: list, pkb: dict) -> list:
    """Score a batch of jobs. Pre-computes candidate skills/domains once.

    Args:
        jobs: List of dicts, each with 'parsed_jd' and optional 'posted_days_ago'.
        pkb: Full PKB dict.

    Returns:
        Same list with 'score' dict added to each job.
    """
    candidate_skills = _build_candidate_skills(pkb)
    candidate_domains = _build_candidate_domains(pkb)

    for job in jobs:
        parsed_jd = job.get("parsed_jd")
        if not parsed_jd:
            job["score"] = {"fit_score": 0, "recommendation": "SKIP",
                            "components": {}, "missing_critical_skills": [],
                            "error": "no_parsed_jd"}
            continue

        days_ago = job.get("posted_days_ago")
        jd_text = job.get("description")
        job["score"] = score_job(
            parsed_jd, pkb, days_ago,
            candidate_skills=candidate_skills,
            candidate_domains=candidate_domains,
            jd_text=jd_text,
        )

    # Sort by fit_score descending
    jobs.sort(key=lambda j: j.get("score", {}).get("fit_score", 0), reverse=True)
    return jobs


def load_pkb(pkb_path: str = None) -> dict:
    """Load PKB from file."""
    if pkb_path is None:
        pkb_path = os.path.join(os.path.dirname(__file__), "..", "data", "pkb.json")
    with open(pkb_path) as f:
        return json.load(f)


# ===================================================================
# CLI testing
# ===================================================================

def _run_title_tests() -> bool:
    """Run title scoring tests. Returns True if all pass."""
    test_cases = [
        ("Senior Product Manager, CRM", 20, "current_level"),
        ("Principal PM, Fintech Platform", 20, "target_level"),
        ("Lead Product Manager, AI", 20, "target_level"),
        ("Director of Product Management", 15, "stretch_level"),
        ("VP of Product", 3, "too_senior"),
        ("Associate PM", 0, "demotion"),
    ]
    all_pass = True
    for jd_title, expected_score, expected_label in test_cases:
        result = title_match_score(jd_title, [])
        ok = result["score"] == expected_score and result["label"] == expected_label
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: '{jd_title}' -> {result['score']}/20, label={result['label']} (expected {expected_score}, {expected_label})")
    return all_pass


def _run_experience_tests() -> bool:
    """Run experience compatibility tests. Returns True if all pass."""
    test_cases = [
        ("9 years of product management experience required", 1.0),
        ("Minimum 10 years of PM experience", 0.97),
        ("11+ years of product management experience", 0.90),
        ("13 years minimum experience in product", 0.40),
    ]
    all_pass = True
    for jd_snippet, expected_mult in test_cases:
        result = experience_compatibility(jd_snippet, {})
        ok = abs(result["multiplier"] - expected_mult) < 0.01
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: '{jd_snippet[:50]}...' -> multiplier={result['multiplier']} (expected ~{expected_mult})")
    return all_pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run title scoring tests")
    args = parser.parse_args()

    if args.test:
        print("Title scoring tests:")
        title_ok = _run_title_tests()
        print("\nExperience compatibility tests:")
        exp_ok = _run_experience_tests()
        sys.exit(0 if (title_ok and exp_ok) else 1)

    # Quick test: score a sample parsed JD
    pkb = load_pkb()

    sample_jd = {
        "job_title": "Senior Product Manager, CRM Platform",
        "company": "Freshworks",
        "location": "Remote, India",
        "industry_terms": [
            {"term": "CRM", "priority": "P0"},
            {"term": "SaaS", "priority": "P0"},
            {"term": "enterprise", "priority": "P0"},
        ],
        "company_context": "Leading SaaS company providing customer engagement solutions",
        "p0_keywords": ["CRM", "product roadmap", "cross-functional", "SaaS", "SQL",
                        "A/B testing", "stakeholder management", "enterprise"],
        "p1_keywords": ["agile", "data-driven", "user research", "retention"],
        "p2_keywords": ["Tableau"],
        "job_level": "Senior IC",
    }

    result = score_job(sample_jd, pkb, posted_days_ago=1)
    print(f"\nFit Score: {result['fit_score']}/100")
    print(f"Recommendation: {result['recommendation']}")
    print(f"\nComponents:")
    for name, comp in result["components"].items():
        if "score" in comp and "max" in comp:
            print(f"  {name}: {comp['score']}/{comp['max']}")
        elif name == "experience_compatibility":
            print(f"  {name}: multiplier={comp.get('multiplier', 1.0)}, {comp.get('note', '')}")
    if result["missing_critical_skills"]:
        print(f"\nMissing P0: {result['missing_critical_skills']}")
