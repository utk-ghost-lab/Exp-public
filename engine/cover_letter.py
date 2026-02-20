"""Cover letter generation using Claude API.

Generates a 3-4 paragraph cover letter tailored to the JD, referencing
specific achievements from the resume and explaining "why this company."
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def generate_cover_letter(
    parsed_jd: dict,
    pkb: dict,
    resume_content: dict,
    research_brief: dict | None = None,
) -> dict:
    """Generate a tailored cover letter.

    Args:
        parsed_jd: Parsed job description (from jd_parser or jd_parse_and_map)
        pkb: Profile Knowledge Base
        resume_content: Resume content dict (may contain reframing_log)
        research_brief: Optional research brief about the company

    Returns:
        dict with "text" key containing the cover letter text
    """
    import anthropic
    from engine.api_utils import messages_create_with_retry

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Extract key info
    candidate_name = (pkb.get("personal_info") or {}).get("name", "the candidate")
    job_title = parsed_jd.get("job_title") or parsed_jd.get("title", "the role")
    company = parsed_jd.get("company", "the company")

    # Build context about the role
    responsibilities = parsed_jd.get("key_responsibilities", [])
    if isinstance(responsibilities, list):
        resp_text = "\n".join(f"- {r}" for r in responsibilities[:5])
    else:
        resp_text = str(responsibilities)

    # Build achievements context from resume/PKB
    achievements = []
    for exp in (pkb.get("work_experience") or [])[:3]:
        for bullet in (exp.get("bullets") or [])[:2]:
            text = bullet if isinstance(bullet, str) else bullet.get("original_text", "")
            if text:
                achievements.append(text)

    achievements_text = "\n".join(f"- {a}" for a in achievements[:5])

    # Company research context
    company_context = ""
    if research_brief:
        company_context = f"\nCompany research: {research_brief.get('summary', '')}"
    elif parsed_jd.get("company_context"):
        company_context = f"\nCompany context: {parsed_jd['company_context']}"

    prompt = f"""Write a professional cover letter for the following job application.

CANDIDATE: {candidate_name}
TARGET ROLE: {job_title} at {company}
{company_context}

KEY RESPONSIBILITIES OF THE ROLE:
{resp_text}

CANDIDATE'S KEY ACHIEVEMENTS:
{achievements_text}

RULES:
1. 3-4 paragraphs maximum
2. Opening paragraph: Express interest in the specific role, mention the company by name
3. Body paragraphs: Reference 1-2 specific achievements that directly relate to the role's requirements. Use concrete numbers/metrics.
4. Closing paragraph: Explain briefly why this company (not generic), express enthusiasm for contributing
5. Tone: Confident, specific, human — not generic or AI-sounding
6. Do NOT use phrases like "I am writing to express my interest" or "I believe I would be a great fit"
7. Do NOT repeat the entire resume — pick the 1-2 most relevant achievements
8. Keep it under 400 words
9. Do not include any headers, addresses, or date — just the body text
10. Sign off with just the candidate's name

Write the cover letter now:"""

    response = messages_create_with_retry(
        client,
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    return {"text": text}
