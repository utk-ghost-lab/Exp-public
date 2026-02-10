"""Step 2a: Profile Knowledge Base Builder

One-time setup: reads all documents in profile/ and builds a structured PKB.

Input: All files in profile/ folder (PDF, DOCX, MD, TXT)
Output: data/pkb.json
"""

import json
import logging
import os

import anthropic
import pdfplumber

logger = logging.getLogger(__name__)

PKB_SCHEMA = {
    "personal_info": {
        "name": "",
        "email": "",
        "phone": "",
        "location": "",
        "linkedin_url": "",
        "portfolio_url": "",
    },
    "work_experience": [],
    "skills": {
        "hard_skills": [],
        "soft_skills": [],
        "tools": [],
        "methodologies": [],
        "domains": [],
    },
    "education": [],
    "certifications": [],
    "projects": [],
    "achievements": [],
    "all_experience_keywords": [],
}

EXTRACTION_PROMPT = """You are a career data extraction engine. You will receive raw text extracted from multiple career documents (resumes, LinkedIn profiles, CVs) belonging to the SAME person. The documents may have overlapping content — deduplicate and consolidate into a single comprehensive profile.

Extract EVERY piece of career information exhaustively and return a JSON object matching this EXACT structure:

{
  "personal_info": {
    "name": "Full name",
    "email": "email address",
    "phone": "phone number",
    "location": "city, state/country",
    "linkedin_url": "full LinkedIn URL",
    "portfolio_url": "portfolio/website URL if any"
  },
  "work_experience": [
    {
      "company": "Company Name",
      "title": "Job Title",
      "dates": {"start": "Mon YYYY", "end": "Mon YYYY or Present"},
      "duration_months": 24,
      "location": "City, Country",
      "company_description": "Brief description of what the company does",
      "bullets": [
        {
          "original_text": "The exact bullet point text from the document",
          "skills_demonstrated": ["skill1", "skill2"],
          "tools_used": ["tool1", "tool2"],
          "metrics": ["specific metric mentioned"],
          "domain": "industry domain"
        }
      ],
      "industry": "primary industry",
      "company_size": "size info if available"
    }
  ],
  "skills": {
    "hard_skills": ["list of technical/hard skills"],
    "soft_skills": ["list of soft/leadership skills"],
    "tools": ["specific tools, software, platforms"],
    "methodologies": ["frameworks and methodologies"],
    "domains": ["industry domains and verticals"]
  },
  "education": [
    {
      "institution": "School name",
      "degree": "Degree type",
      "field": "Field of study",
      "dates": {"start": "YYYY", "end": "YYYY"},
      "location": "Location if available"
    }
  ],
  "certifications": [
    {
      "name": "Certification name",
      "issuer": "Issuing organization"
    }
  ],
  "projects": [
    {
      "name": "Project name",
      "description": "What it was",
      "outcomes": ["measurable outcomes"],
      "skills_used": ["relevant skills"]
    }
  ],
  "achievements": [
    {
      "title": "Achievement title",
      "description": "Details",
      "context": "Company or institution where achieved"
    }
  ],
  "all_experience_keywords": ["comprehensive flat list of EVERY skill, tool, technology, methodology, domain term, and business concept found across all documents"]
}

CRITICAL RULES:
1. DEDUPLICATE: If the same role appears in multiple documents, merge the bullets — take the UNION of all unique bullets.
2. CONSOLIDATE SKILLS: Combine skills from all documents into one list per category. No duplicates.
3. PRESERVE METRICS: Every number, percentage, dollar amount, team size — capture it exactly.
4. CAPTURE EVERYTHING: Even if a bullet seems minor, include it. The reframing engine needs maximum raw material.
5. WORK EXPERIENCE ORDER: Most recent first (reverse chronological).
6. DURATION: Calculate duration_months as accurately as possible from the dates.
7. ALL_EXPERIENCE_KEYWORDS: This should be an exhaustive flat list of every relevant keyword. Include variations (e.g., both "A/B testing" and "A/B tests"). This list is used for keyword matching later.
8. For bullets, extract skills_demonstrated, tools_used, and metrics even if they require inference from context.

Return ONLY the JSON object. No markdown, no explanation."""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    text_parts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        logger.error(f"Failed to read PDF {pdf_path}: {e}")
        return ""
    return "\n\n".join(text_parts)


def extract_text_from_file(file_path: str) -> str:
    """Extract text from a file based on its extension."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in (".md", ".txt"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    elif ext == ".docx":
        try:
            from docx import Document

            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            logger.error(f"Failed to read DOCX {file_path}: {e}")
            return ""
    else:
        logger.warning(f"Unsupported file type: {ext} for {file_path}")
        return ""


def read_all_profile_documents(profile_dir: str) -> str:
    """Read all documents in the profile directory and combine their text."""
    all_text = []
    supported_extensions = {".pdf", ".docx", ".md", ".txt"}

    if not os.path.exists(profile_dir):
        raise FileNotFoundError(f"Profile directory not found: {profile_dir}")

    files = sorted(os.listdir(profile_dir))
    doc_count = 0

    for filename in files:
        if filename.startswith("."):
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in supported_extensions:
            continue

        file_path = os.path.join(profile_dir, filename)
        logger.info(f"Reading: {filename}")
        text = extract_text_from_file(file_path)

        if text.strip():
            all_text.append(f"=== DOCUMENT: {filename} ===\n{text}")
            doc_count += 1

    if doc_count == 0:
        raise ValueError(f"No readable documents found in {profile_dir}")

    logger.info(f"Read {doc_count} documents from {profile_dir}")
    return "\n\n" + "\n\n".join(all_text)


def extract_pkb_with_llm(combined_text: str) -> dict:
    """Use Claude API to extract structured PKB from combined document text."""
    client = anthropic.Anthropic()

    logger.info("Sending documents to Claude for PKB extraction...")
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\n---\n\nDOCUMENTS:\n{combined_text}",
            }
        ],
    )

    response_text = message.content[0].text.strip()

    # Parse JSON from response (handle potential markdown wrapping)
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        # Remove first and last lines (``` markers)
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
        pkb = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.error(f"Response preview: {response_text[:500]}")
        raise ValueError("LLM returned invalid JSON. Check logs for details.") from e

    return pkb


def validate_pkb(pkb: dict) -> list:
    """Validate PKB has all required top-level fields. Returns list of warnings."""
    warnings = []
    required_fields = [
        "personal_info",
        "work_experience",
        "skills",
        "education",
        "certifications",
        "achievements",
        "all_experience_keywords",
    ]

    for field in required_fields:
        if field not in pkb:
            warnings.append(f"Missing required field: {field}")

    # Validate personal_info
    if "personal_info" in pkb:
        for key in ["name", "email"]:
            if not pkb["personal_info"].get(key):
                warnings.append(f"Missing personal_info.{key}")

    # Validate work_experience has entries
    if "work_experience" in pkb and len(pkb["work_experience"]) == 0:
        warnings.append("work_experience is empty")

    # Validate each work experience entry has bullets
    for i, exp in enumerate(pkb.get("work_experience", [])):
        if not exp.get("bullets"):
            warnings.append(f"work_experience[{i}] ({exp.get('company', 'unknown')}) has no bullets")

    # Validate skills categories
    if "skills" in pkb:
        for category in ["hard_skills", "soft_skills", "tools", "methodologies", "domains"]:
            if not pkb["skills"].get(category):
                warnings.append(f"skills.{category} is empty")

    # Validate all_experience_keywords
    if "all_experience_keywords" in pkb and len(pkb["all_experience_keywords"]) < 10:
        warnings.append("all_experience_keywords seems too short (< 10 entries)")

    return warnings


def build_pkb(profile_dir: str = "profile", output_path: str = "data/pkb.json") -> dict:
    """Build the Profile Knowledge Base from career documents.

    Args:
        profile_dir: Path to directory containing career documents
        output_path: Where to save the generated PKB JSON

    Returns:
        The PKB dict
    """
    # Step 1: Read all documents
    logger.info("Reading profile documents...")
    combined_text = read_all_profile_documents(profile_dir)
    logger.info(f"Combined text length: {len(combined_text)} characters")

    # Step 2: Extract structured data via LLM
    pkb = extract_pkb_with_llm(combined_text)

    # Step 3: Validate
    warnings = validate_pkb(pkb)
    if warnings:
        logger.warning("PKB validation warnings:")
        for w in warnings:
            logger.warning(f"  - {w}")
    else:
        logger.info("PKB validation passed — all fields present")

    # Step 4: Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(pkb, f, indent=2, ensure_ascii=False)
    logger.info(f"PKB saved to {output_path}")

    # Log summary stats
    logger.info(f"  Work experiences: {len(pkb.get('work_experience', []))}")
    logger.info(f"  Total bullets: {sum(len(e.get('bullets', [])) for e in pkb.get('work_experience', []))}")
    logger.info(f"  Skills keywords: {len(pkb.get('all_experience_keywords', []))}")
    logger.info(f"  Education entries: {len(pkb.get('education', []))}")
    logger.info(f"  Certifications: {len(pkb.get('certifications', []))}")
    logger.info(f"  Achievements: {len(pkb.get('achievements', []))}")

    return pkb
