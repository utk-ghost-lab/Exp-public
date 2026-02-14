"""Step 2: JD Deep Parse

Parses a job description into structured buckets with prioritized keywords.

Input: Job description text (raw string or URL)
Output: Structured JD analysis dict with skills, requirements, and keyword priorities
"""

import json
import logging

import anthropic

from engine.api_utils import messages_create_with_retry
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

JD_PARSE_PROMPT = """You are a job description analysis engine for ATS resume optimization. Parse the following job description into a structured analysis.

Extract and categorize into these EXACT buckets:

1. **hard_skills**: Specific tools, technologies, methodologies (e.g., "SQL", "A/B testing", "Agile")
2. **soft_skills**: Leadership, communication, collaboration signals (e.g., "cross-functional leadership")
3. **industry_terms**: Domain vocabulary (e.g., "SaaS", "CRM", "fintech", "marketplace")
4. **experience_requirements**: Years, seniority, specific experiences (e.g., "5+ years PM experience")
5. **education_requirements**: Degrees, certifications
6. **key_responsibilities**: What the role actually does day-to-day
7. **achievement_language**: What kind of results they want (e.g., "drove growth", "increased retention")
8. **company_context**: What the company does, team, stage
9. **job_level**: Seniority signals (Senior, Lead, Director, IC)
10. **cultural_signals**: Values and culture markers (e.g., "data-driven", "move fast")

For each skill/term, assign priority STRICTLY:

- **P0 (Must-Have)** — STRICTLY LIMITED. Only include a keyword as P0 if it meets ONE of:
  1. It appears in the **job title**
  2. It appears in an explicit **Requirements** / **What you need** / **What skills and experience do you need** section
  3. It is **repeated 2 or more times** anywhere in the JD (true deal-breakers the employer emphasizes)
  A typical JD should have **8–15 P0 keywords total**. If you have more than 15, demote the rest to P1. P0 are true deal-breakers only.

- **P1 (Should-Have)**: Keywords mentioned **once** in the main description body, responsibilities section, or role description. Not in title or requirements section, and not repeated 2+ times.

- **P2 (Nice-to-Have)**: Keywords **only** in "Preferred", "Bonus", "Plus", "Nice to have" or equivalent sections. Do not put main body or responsibility keywords here.

CRITICAL: Extract the EXACT phrases as they appear in the JD for ATS exact-match. Do not paraphrase. P0 count must be 8–15 for a typical JD.

Return this EXACT JSON structure:
{
  "job_title": "exact title from JD",
  "company": "Company Name",
  "location": "location info",
  "hard_skills": [
    {"skill": "exact skill", "priority": "P0", "original_phrase": "exact phrase from JD containing this skill"}
  ],
  "soft_skills": [
    {"skill": "exact skill", "priority": "P0", "original_phrase": "exact phrase from JD"}
  ],
  "industry_terms": [
    {"term": "exact term", "priority": "P0"}
  ],
  "experience_requirements": [
    {"requirement": "exact requirement", "priority": "P0"}
  ],
  "education_requirements": [
    {"requirement": "exact requirement", "priority": "P0 or P2"}
  ],
  "key_responsibilities": ["responsibility 1", "responsibility 2"],
  "achievement_language": ["exact phrases about desired outcomes/results"],
  "company_context": "brief description of company, stage, size, industry",
  "job_level": "detected seniority level",
  "cultural_signals": ["signal 1", "signal 2"],
  "all_keywords_flat": ["every single keyword extracted, deduplicated, in a flat list"],
  "p0_keywords": ["only P0 keywords"],
  "p1_keywords": ["only P1 keywords"],
  "p2_keywords": ["only P2 keywords"]
}

RULES:
1. Be EXHAUSTIVE in all_keywords_flat — capture every skill, tool, technology, and domain term mentioned.
2. Preserve original phrasing exactly for ATS matching.
3. Include both abbreviated and full forms in all_keywords_flat (e.g., "PM" and "Product Manager").
4. For compound skills, include both the compound and individual parts in all_keywords_flat.
5. P0 MUST be 8–15 keywords: only job title, requirements section, or repeated 2+ times. If you have 20+ P0, you are over-classifying — demote to P1.
6. P1 = mentioned once in body/responsibilities; P2 = only in preferred/bonus/plus sections.
7. When a section says "plus" or "preferred" or "nice to have", those keywords are P2 only.
8. all_keywords_flat should be comprehensive — this is used for keyword matching later.

Return ONLY the JSON object. No markdown, no explanation."""


def parse_jd(jd_text: str) -> dict:
    """Parse a job description into structured analysis.

    Args:
        jd_text: Raw job description text

    Returns:
        Structured dict with categorized keywords and priorities
    """
    if not jd_text or not jd_text.strip():
        raise ValueError("Job description text is empty")

    client = anthropic.Anthropic()

    logger.info("Parsing job description with Claude...")
    message = messages_create_with_retry(
        client,
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        timeout=60.0,
        messages=[
            {
                "role": "user",
                "content": f"{JD_PARSE_PROMPT}\n\n---\n\nJOB DESCRIPTION:\n{jd_text}",
            }
        ],
    )

    response_text = message.content[0].text.strip()

    # Handle potential markdown wrapping
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        json_lines = []
        in_json = False
        for line in lines:
            if line.strip().startswith("```") and not in_json:
                in_json = True
                continue
            elif line.strip() == "```":
                break
            elif in_json:
                json_lines.append(line)
        response_text = "\n".join(json_lines)

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JD analysis as JSON: {e}")
        logger.error(f"Response preview: {response_text[:500]}")
        raise ValueError("LLM returned invalid JSON for JD parsing.") from e

    # Enforce P0 = 8-15: reclassify using raw JD text (title + requirements + repeated 2+ only)
    parsed = reclassify_priorities_from_jd_text(parsed, jd_text, max_p0=15)

    # Validate required fields
    warnings = validate_parsed_jd(parsed)
    if warnings:
        logger.warning("JD parse validation warnings:")
        for w in warnings:
            logger.warning(f"  - {w}")
    else:
        logger.info("JD parse validation passed")

    # Log summary
    logger.info(f"  Job: {parsed.get('job_title', 'Unknown')} at {parsed.get('company', 'Unknown')}")
    logger.info(f"  P0 keywords: {len(parsed.get('p0_keywords', []))}")
    logger.info(f"  P1 keywords: {len(parsed.get('p1_keywords', []))}")
    logger.info(f"  P2 keywords: {len(parsed.get('p2_keywords', []))}")
    logger.info(f"  Total keywords: {len(parsed.get('all_keywords_flat', []))}")

    return parsed


def validate_parsed_jd(parsed: dict) -> list:
    """Validate parsed JD has all required fields. Returns list of warnings."""
    warnings = []
    required_fields = [
        "job_title", "company", "hard_skills", "soft_skills",
        "industry_terms", "experience_requirements", "key_responsibilities",
        "achievement_language", "company_context", "job_level",
        "cultural_signals", "all_keywords_flat", "p0_keywords",
    ]

    for field in required_fields:
        if field not in parsed:
            warnings.append(f"Missing field: {field}")
        elif isinstance(parsed[field], list) and len(parsed[field]) == 0:
            warnings.append(f"Empty list: {field}")

    if not parsed.get("job_title"):
        warnings.append("job_title is empty")

    p0_count = len(parsed.get("p0_keywords", []))
    if p0_count < 5:
        warnings.append("Too few P0 keywords (< 5)")
    if p0_count > 15:
        warnings.append("Too many P0 keywords (> 15); P0 must be 8-15 (title, requirements, or repeated 2+)")

    if len(parsed.get("all_keywords_flat", [])) < 10:
        warnings.append("Too few total keywords (< 10)")

    return warnings


def scrape_jd_from_url(url: str) -> str:
    """Scrape job description text from a URL."""
    logger.info(f"Scraping JD from: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    text = soup.get_text(separator="\n", strip=True)

    if len(text) < 100:
        raise ValueError(f"Scraped text too short ({len(text)} chars) — page may require JS rendering")

    logger.info(f"Scraped {len(text)} characters from URL")
    return text


def reclassify_priorities_from_jd_text(parsed: dict, jd_text: str, max_p0: int = 15) -> dict:
    """Reclassify P0/P1/P2 using raw JD text: P0 = title + requirements section + repeated 2+; cap P0 at max_p0.
    Use when LLM over-classified P0. Requires parsed to have p0_keywords, p1_keywords, p2_keywords or derived from hard_skills etc."""
    import re
    jd_lower = jd_text.lower()
    # Requirements block: from "what skills" or "experience" to "equal employment" or end
    req_start = re.search(r"what skills and experience|experience\s*$|requirements|what you need", jd_lower, re.I)
    req_end = re.search(r"equal employment|zenoti provides equal", jd_lower, re.I)
    requirements_section = ""
    if req_start:
        start = req_start.start()
        end = req_end.start() if req_end else len(jd_text)
        requirements_section = jd_text[start:end].lower()
    title = (parsed.get("job_title") or "").lower()

    # Collect all keywords with current priority
    all_kw = list(dict.fromkeys(
        (parsed.get("p0_keywords") or []) +
        (parsed.get("p1_keywords") or []) +
        (parsed.get("p2_keywords") or [])
    ))
    if not all_kw:
        for item in (parsed.get("hard_skills") or []) + (parsed.get("soft_skills") or []):
            s = (item.get("skill") or item.get("term") or "").strip()
            if s and s not in all_kw:
                all_kw.append(s)
    if not all_kw:
        return parsed

    p0_candidates = []
    for kw in all_kw:
        if not kw or len(kw) < 2:
            continue
        kw_lower = kw.lower()
        count = len(re.findall(re.escape(kw_lower), jd_lower))
        in_title = kw_lower in title
        in_req = kw_lower in requirements_section
        if in_title or in_req or count >= 2:
            p0_candidates.append((kw, count, in_title, in_req))
    # Sort by: in title first, then in req, then by count. Take top max_p0.
    p0_candidates.sort(key=lambda x: (x[2], x[3], x[1]), reverse=True)
    new_p0 = list(dict.fromkeys(k[0] for k in p0_candidates[: max_p0 * 2]))[:max_p0]  # dedupe, then cap
    if len(new_p0) < 5 and p0_candidates:
        new_p0 = list(dict.fromkeys(k[0] for k in p0_candidates))[:max_p0]
    p0_set = set(new_p0)
    new_p1 = [k for k in all_kw if k and k not in p0_set]
    new_p2 = [k for k in (parsed.get("p2_keywords") or []) if k and k not in p0_set and k not in new_p1]
    # P2: only if in "plus"/"preferred" snippet
    plus_section = ""
    if re.search(r"is a plus|preferred|nice to have|bonus", jd_lower):
        for m in re.finditer(r".{0,200}(?:plus|preferred|nice to have|bonus).{0,300}", jd_lower, re.I | re.DOTALL):
            plus_section += m.group(0)
    p2_set = set()
    for kw in new_p1[:]:
        if kw and kw.lower() in plus_section and "product manager" not in kw.lower():
            p2_set.add(kw)
    new_p1 = [k for k in new_p1 if k not in p2_set]
    new_p2 = list(p2_set) + [k for k in (parsed.get("p2_keywords") or []) if k and k not in p0_set]
    new_p2 = list(dict.fromkeys(new_p2))

    out = dict(parsed)
    out["p0_keywords"] = new_p0
    out["p1_keywords"] = new_p1
    out["p2_keywords"] = new_p2
    out["all_keywords_flat"] = list(dict.fromkeys(new_p0 + new_p1 + new_p2))
    logger.info("Reclassified priorities from JD text: P0=%d, P1=%d, P2=%d", len(new_p0), len(new_p1), len(new_p2))
    return out


def parse_jd_from_url(url: str) -> dict:
    """Scrape a job description from URL and parse it.

    Args:
        url: URL to job posting

    Returns:
        Structured dict with categorized keywords and priorities
    """
    jd_text = scrape_jd_from_url(url)
    return parse_jd(jd_text)
