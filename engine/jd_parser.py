"""Step 2: JD Deep Parse

Parses a job description into structured buckets with prioritized keywords.

Input: Job description text (raw string or URL)
Output: Structured JD analysis dict with skills, requirements, and keyword priorities
"""

import json
import logging

import anthropic
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

For each skill/term, assign priority:
- **P0 (Must-Have)**: Keywords in job title, first paragraph, or "Requirements"/"What you need" section. These are deal-breakers.
- **P1 (Should-Have)**: Keywords in description body, mentioned once in responsibilities
- **P2 (Nice-to-Have)**: Keywords in "Preferred"/"Bonus"/"Plus" sections

CRITICAL: Extract the EXACT phrases as they appear in the JD for ATS exact-match. Do not paraphrase.

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
1. Be EXHAUSTIVE — capture every skill, tool, technology, and domain term mentioned.
2. Preserve original phrasing exactly for ATS matching.
3. Include both abbreviated and full forms (e.g., "PM" and "Product Manager").
4. For compound skills, include both the compound and individual parts (e.g., "B2B SaaS" → also "B2B", "SaaS").
5. Keywords in the title or requirements section are always P0.
6. When a section says "plus" or "preferred" or "nice to have", those are P2.
7. all_keywords_flat should be comprehensive — this is used for keyword matching later.

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
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=8000,
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

    if len(parsed.get("p0_keywords", [])) < 3:
        warnings.append("Too few P0 keywords (< 3)")

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


def parse_jd_from_url(url: str) -> dict:
    """Scrape a job description from URL and parse it.

    Args:
        url: URL to job posting

    Returns:
        Structured dict with categorized keywords and priorities
    """
    jd_text = scrape_jd_from_url(url)
    return parse_jd(jd_text)
