"""Step 3: Profile Mapper

Maps JD requirements to user experience from the PKB.
Classifies each mapping as DIRECT, ADJACENT, TRANSFERABLE, or GAP.

Input: Parsed JD (from Step 1) + pkb.json
Output: Mapping matrix with reframe strategies and coverage summary
"""

import json
import logging
import re
import time

import anthropic

logger = logging.getLogger(__name__)

# Cap keywords to prevent token overflow (Bug 4 fix)
MAX_P1_KEYWORDS_FOR_MAPPER = 25

MAPPING_PROMPT = """You are an expert career strategist and resume optimization engine. Your job is to map job description requirements to a candidate's actual experience from their Profile Knowledge Base (PKB).

For EACH significant requirement/skill/keyword in the JD, classify the match:

- **DIRECT**: Candidate has this exact skill/experience explicitly in their PKB (e.g., JD says "SQL", candidate lists "SQL")
- **ADJACENT**: Candidate has something closely related that can be legitimately reframed (e.g., JD says "CRM strategy", candidate did "customer lifecycle management" — same domain, different framing)
- **TRANSFERABLE**: Candidate did this in a different context/industry (e.g., JD wants "beauty & wellness" domain, candidate has "fintech" but similar workflow patterns)
- **GAP**: Candidate genuinely doesn't have this skill or any close equivalent

For ADJACENT and TRANSFERABLE matches, generate a specific reframing strategy that is:
1. Interview-defensible (candidate can honestly discuss it)
2. Uses JD language naturally
3. Based on real work the candidate did

IMPORTANT RULES:
1. Be thorough — map EVERY P0 keyword and most P1 keywords
2. Be honest — don't force DIRECT matches where there aren't any. GAPs are fine.
3. For each mapping, identify the BEST source experience (specific company + bullet) from the PKB
4. Confidence score: 1.0 = perfect match, 0.8 = strong adjacent, 0.6 = transferable, 0.3 = weak
5. interview_defensible: true only if the candidate can honestly discuss this in an interview

Return this EXACT JSON structure:
{
  "mappings": [
    {
      "jd_requirement": "exact keyword or requirement from JD",
      "priority": "P0/P1/P2",
      "match_type": "DIRECT/ADJACENT/TRANSFERABLE/GAP",
      "source_experience": {
        "company": "Company name from PKB",
        "bullet": "The specific bullet text that supports this match",
        "skills": ["relevant skills from this bullet"]
      },
      "reframe_strategy": "For ADJACENT/TRANSFERABLE: specific strategy for how to reframe. For DIRECT: null. For GAP: null.",
      "confidence": 0.8,
      "interview_defensible": true
    }
  ],
  "coverage_summary": {
    "p0_covered": 0,
    "p0_total": 0,
    "p0_coverage_pct": 0,
    "p1_covered": 0,
    "p1_total": 0,
    "p1_coverage_pct": 0,
    "overall_match_score": 0,
    "direct_count": 0,
    "adjacent_count": 0,
    "transferable_count": 0,
    "gap_count": 0,
    "gaps": ["list of P0/P1 keywords that are genuine gaps"],
    "strongest_matches": ["list of top 5 strongest DIRECT matches"],
    "best_reframe_opportunities": ["list of top 3 ADJACENT matches with high confidence"]
  }
}

Map ALL P0 keywords and ALL P1 keywords. Skip P2. Be concise — keep reframe_strategy to 1 sentence max.

Return ONLY the JSON object. No markdown, no explanation."""


def _try_repair_json(text: str):
    """Attempt to repair truncated JSON from LLM response.

    Common truncation: response cut mid-string or mid-object.
    Strategy: close open strings/arrays/objects progressively.
    """
    if not text or not text.strip():
        return None
    s = text.strip()
    # Try parsing as-is first
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Count unclosed braces/brackets
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    # Check if we're inside a string (odd number of unescaped quotes)
    in_string = s.count('"') % 2 == 1
    repaired = s
    if in_string:
        repaired += '"'
    # Close any open value (if truncated mid-value after a colon)
    if repaired.rstrip().endswith((",", ":")):
        repaired = repaired.rstrip().rstrip(",:")
    repaired += "]" * max(0, open_brackets + (1 if in_string else 0))
    repaired += "}" * max(0, open_braces)
    try:
        result = json.loads(repaired)
        logger.info("JSON repair succeeded (closed %d braces, %d brackets)", open_braces, open_brackets)
        return result
    except json.JSONDecodeError:
        pass
    # Last resort: find the last valid top-level object
    for end_pos in range(len(s) - 1, max(0, len(s) // 2), -1):
        candidate = s[:end_pos]
        open_b = candidate.count("{") - candidate.count("}")
        open_br = candidate.count("[") - candidate.count("]")
        in_str = candidate.count('"') % 2 == 1
        attempt = candidate
        if in_str:
            attempt += '"'
        attempt += "]" * max(0, open_br)
        attempt += "}" * max(0, open_b)
        try:
            result = json.loads(attempt)
            logger.info("JSON repair succeeded by truncating to position %d", end_pos)
            return result
        except json.JSONDecodeError:
            continue
    return None


def map_profile_to_jd(parsed_jd: dict, pkb: dict) -> dict:
    """Map JD requirements to user's experience in PKB.

    Args:
        parsed_jd: Structured JD analysis from jd_parser
        pkb: Profile Knowledge Base dict

    Returns:
        Mapping matrix with match types, reframe strategies, and coverage
    """
    client = anthropic.Anthropic()

    # Bug 4 fix: Cap P1 keywords to prevent token overflow; drop P2 entirely
    p0_keywords = parsed_jd.get("p0_keywords", [])
    p1_keywords = parsed_jd.get("p1_keywords", [])[:MAX_P1_KEYWORDS_FOR_MAPPER]
    p2_keywords = []  # Skip P2 to reduce payload
    if len(parsed_jd.get("p1_keywords", [])) > MAX_P1_KEYWORDS_FOR_MAPPER:
        logger.info(
            "Capped P1 keywords from %d to %d for mapper (dropped P2 entirely)",
            len(parsed_jd.get("p1_keywords", [])), MAX_P1_KEYWORDS_FOR_MAPPER,
        )

    # Build a focused context with the JD keywords and PKB
    jd_summary = json.dumps({
        "job_title": parsed_jd.get("job_title"),
        "company": parsed_jd.get("company"),
        "hard_skills": parsed_jd.get("hard_skills", []),
        "soft_skills": parsed_jd.get("soft_skills", []),
        "industry_terms": parsed_jd.get("industry_terms", []),
        "experience_requirements": parsed_jd.get("experience_requirements", []),
        "education_requirements": parsed_jd.get("education_requirements", []),
        "key_responsibilities": parsed_jd.get("key_responsibilities", []),
        "achievement_language": parsed_jd.get("achievement_language", []),
        "cultural_signals": parsed_jd.get("cultural_signals", []),
        "job_level": parsed_jd.get("job_level"),
        "p0_keywords": p0_keywords,
        "p1_keywords": p1_keywords,
        "p2_keywords": p2_keywords,
    }, indent=2)

    # Condense PKB: only send bullet text (max 6 per role), skills, and company/title/dates
    condensed_pkb = {
        "personal_info": {"name": (pkb.get("personal_info") or {}).get("name", "")},
        "work_experience": [],
        "skills": pkb.get("skills") or {},
    }
    for w in pkb.get("work_experience") or []:
        bullets = []
        for b in (w.get("bullets") or [])[:6]:  # cap at 6 bullets per role
            text = (b.get("original_text") or "") if isinstance(b, dict) else str(b)
            if text.strip():
                bullets.append(text.strip())
        condensed_pkb["work_experience"].append({
            "company": w.get("company"),
            "title": w.get("title"),
            "dates": w.get("dates"),
            "bullets": bullets,
            "industry": w.get("industry", ""),
        })
    pkb_summary = json.dumps(condensed_pkb, indent=2)
    logger.info("Condensed PKB for mapper: %d chars", len(pkb_summary))

    logger.info("Mapping profile to JD requirements with Claude...")

    # Retry logic with JSON repair (Bug 4 fix)
    max_retries = 1
    last_error = None
    response_text = ""
    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=8000,
                timeout=90.0,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{MAPPING_PROMPT}\n\n"
                            f"---\n\nJOB DESCRIPTION ANALYSIS:\n{jd_summary}\n\n"
                            f"---\n\nCANDIDATE PROFILE KNOWLEDGE BASE:\n{pkb_summary}"
                        ),
                    }
                ],
            )
            response_text = message.content[0].text.strip()
            break
        except Exception as e:
            last_error = e
            logger.warning("Profile mapper attempt %d/%d failed: %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(3)
            else:
                raise

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
        mapping = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Initial JSON parse failed: %s — attempting repair...", e)
        mapping = _try_repair_json(response_text)
        if mapping is None:
            logger.error("JSON repair failed. Response preview: %s", response_text[:500])
            raise ValueError("LLM returned invalid JSON for profile mapping.") from e

    # Validate
    warnings = validate_mapping(mapping)
    if warnings:
        logger.warning("Mapping validation warnings:")
        for w in warnings:
            logger.warning(f"  - {w}")
    else:
        logger.info("Mapping validation passed")

    # Log summary
    summary = mapping.get("coverage_summary", {})
    logger.info(f"  P0 coverage: {summary.get('p0_covered', '?')}/{summary.get('p0_total', '?')} ({summary.get('p0_coverage_pct', '?')}%)")
    logger.info(f"  P1 coverage: {summary.get('p1_covered', '?')}/{summary.get('p1_total', '?')} ({summary.get('p1_coverage_pct', '?')}%)")
    logger.info(f"  Match types: {summary.get('direct_count', '?')} direct, {summary.get('adjacent_count', '?')} adjacent, {summary.get('transferable_count', '?')} transferable, {summary.get('gap_count', '?')} gap")
    logger.info(f"  Gaps: {summary.get('gaps', [])}")

    return mapping


def validate_mapping(mapping: dict) -> list:
    """Validate mapping matrix structure. Returns list of warnings."""
    warnings = []

    if "mappings" not in mapping:
        warnings.append("Missing 'mappings' field")
        return warnings

    if len(mapping["mappings"]) < 5:
        warnings.append(f"Too few mappings ({len(mapping['mappings'])}), expected at least 5")

    valid_types = {"DIRECT", "ADJACENT", "TRANSFERABLE", "GAP"}
    for i, m in enumerate(mapping["mappings"]):
        if not m.get("jd_requirement"):
            warnings.append(f"mapping[{i}] missing jd_requirement")
        if m.get("match_type") not in valid_types:
            warnings.append(f"mapping[{i}] invalid match_type: {m.get('match_type')}")
        if m.get("match_type") in ("ADJACENT", "TRANSFERABLE") and not m.get("reframe_strategy"):
            warnings.append(f"mapping[{i}] ({m.get('jd_requirement')}) is {m.get('match_type')} but missing reframe_strategy")
        if m.get("match_type") != "GAP" and not m.get("source_experience"):
            warnings.append(f"mapping[{i}] ({m.get('jd_requirement')}) is {m.get('match_type')} but missing source_experience")

    if "coverage_summary" not in mapping:
        warnings.append("Missing 'coverage_summary' field")
    else:
        summary = mapping["coverage_summary"]
        if "p0_coverage_pct" not in summary:
            warnings.append("coverage_summary missing p0_coverage_pct")
        if "gaps" not in summary:
            warnings.append("coverage_summary missing gaps list")

    return warnings
