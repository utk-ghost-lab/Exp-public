"""LinkedIn message generation using Claude API.

Two message types:
- connection_request: < 300 characters
- inmail: < 1900 characters
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def generate_linkedin_message(
    parsed_jd: dict,
    pkb: dict,
    resume_content: dict,
    message_type: str = "connection_request",
) -> dict:
    """Generate a LinkedIn outreach message.

    Args:
        parsed_jd: Parsed job description
        pkb: Profile Knowledge Base
        resume_content: Resume content dict
        message_type: "connection_request" (< 300 chars) or "inmail" (< 1900 chars)

    Returns:
        dict with "text" key containing the message text
    """
    import anthropic
    from engine.api_utils import messages_create_with_retry

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    candidate_name = (pkb.get("personal_info") or {}).get("name", "")
    first_name = candidate_name.split()[0] if candidate_name else ""
    job_title = parsed_jd.get("job_title") or parsed_jd.get("title", "the role")
    company = parsed_jd.get("company", "the company")

    # Top achievement for quick reference
    top_achievement = ""
    for exp in (pkb.get("work_experience") or [])[:1]:
        for bullet in (exp.get("bullets") or [])[:1]:
            text = bullet if isinstance(bullet, str) else bullet.get("original_text", "")
            if text:
                top_achievement = text
                break

    if message_type == "connection_request":
        char_limit = 300
        prompt = f"""Write a LinkedIn connection request message for reaching out about a job.

CANDIDATE: {first_name} (Product Manager)
TARGET ROLE: {job_title} at {company}
TOP ACHIEVEMENT: {top_achievement}

RULES:
1. MUST be under {char_limit} characters total (this is a hard limit)
2. Be specific — mention the role and company
3. Reference one concrete achievement or relevant experience briefly
4. End with a soft ask (e.g., "would love to connect" or "happy to share more")
5. Sound human and warm, not salesy or desperate
6. Do NOT start with "Hi, I'm..." — start with something about the role or company
7. No greeting line or sign-off — just the message body

Write the connection request message now (under {char_limit} characters):"""
    else:
        char_limit = 1900
        prompt = f"""Write a LinkedIn InMail message for reaching out about a job.

CANDIDATE: {first_name} (Product Manager with 8+ years experience)
TARGET ROLE: {job_title} at {company}
TOP ACHIEVEMENT: {top_achievement}

RULES:
1. MUST be under {char_limit} characters total
2. Opening: Mention the specific role and why it caught your attention
3. Body: Reference 1-2 relevant achievements with metrics
4. Closing: Express interest in learning more, suggest a brief call
5. Sound human and professional, not generic
6. Do NOT use "I am writing to..." or "I believe I would be..."
7. Keep it concise — recruiters skim

Write the InMail message now (under {char_limit} characters):"""

    response = messages_create_with_retry(
        client,
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()

    # Enforce character limit
    if len(text) > char_limit:
        text = text[:char_limit - 3] + "..."

    return {"text": text, "message_type": message_type, "char_count": len(text)}
