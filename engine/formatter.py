"""Step 5: Format Validation & Content Preparation (formatter.py)

Validates resume content against permanent formatting rules before PDF rendering:
- Years check (8+ years in summary)
- Bullet count limits per role position
- Bullet length validation (20-30 words)
- Skills cap (<=25 terms)
- Location format consistency (City, Country)
- Verb variety (no opening verb 3+ times)
- Banned opening verbs
- Tech anachronism check (no LLM/GPT pre-June 2023)
- Page length estimation with auto-trim

Input: Scored resume content (JSON) + JD analysis
Output: validated_content + format_validation report
"""

import copy
import logging
import re

logger = logging.getLogger(__name__)

BANNED_START_PHRASES = (
    "responsible for",
    "helped",
    "assisted",
    "participated",
    "supported",
    "worked on",
    "handled",
    "involved in",
)

PRE_2023_TECH_TERMS = (
    "llm", "llm-powered", "large language model", "gpt",
    "generative ai", "gen ai", "rag", "retrieval-augmented",
)

# Estimated lines per bullet (with wrapping) for page length calc
EST_LINES_PER_BULLET = 2.2
EST_LINES_PER_ROLE_HEADER = 2.5
EST_LINES_SUMMARY = 5
EST_LINES_SKILLS = 4
EST_LINES_EDUCATION_PER_ENTRY = 2
EST_LINES_CERT = 2
EST_LINES_AWARDS = 4
EST_LINES_SECTION_HEADER = 2.5
EST_LINES_PAGE_HEADER = 5
LINES_PER_PAGE = 52  # approximate for A4 with specified margins/fonts


def _word_count(text: str) -> int:
    return len((text or "").split())


def _role_end_before_june_2023(role: dict) -> bool:
    dates = role.get("dates") or ""
    if isinstance(dates, dict):
        end = dates.get("end") or ""
    else:
        end = str(dates)
    years = re.findall(r"20\d{2}|19\d{2}", end)
    if not years:
        return False
    end_year = int(max(years))
    if end_year < 2023:
        return True
    if end_year > 2023:
        return False
    if re.search(r"Jan|Feb|Mar|Apr|May", end, re.I):
        return True
    return False


def _is_internship(role: dict) -> bool:
    title = (role.get("title") or "").lower()
    return "intern" in title


def _estimate_page_length(content: dict) -> float:
    total_lines = EST_LINES_PAGE_HEADER

    # Summary section
    if content.get("professional_summary"):
        total_lines += EST_LINES_SECTION_HEADER + EST_LINES_SUMMARY

    # Experience section
    work = content.get("work_experience") or []
    if work:
        total_lines += EST_LINES_SECTION_HEADER
        for role in work:
            total_lines += EST_LINES_PER_ROLE_HEADER
            bullets = role.get("bullets") or []
            total_lines += len(bullets) * EST_LINES_PER_BULLET

    # Skills section
    if content.get("skills"):
        total_lines += EST_LINES_SECTION_HEADER + EST_LINES_SKILLS

    # Awards
    if content.get("awards"):
        total_lines += EST_LINES_SECTION_HEADER + EST_LINES_AWARDS

    # Education
    edu = content.get("education") or []
    if edu:
        total_lines += EST_LINES_SECTION_HEADER
        total_lines += len(edu) * EST_LINES_EDUCATION_PER_ENTRY

    # Certifications
    if content.get("certifications"):
        total_lines += EST_LINES_SECTION_HEADER + EST_LINES_CERT

    return total_lines / LINES_PER_PAGE


def format_resume(resume_content: dict, jd_analysis: dict) -> dict:
    """Validate and prepare resume content for rendering.

    Args:
        resume_content: Scored resume content JSON (from reframer/scorer)
        jd_analysis: Parsed JD analysis

    Returns:
        Dict with validated_content and format_validation report
    """
    content = copy.deepcopy(resume_content)
    # Strip internal keys not needed for rendering
    for key in ("rule13_self_check", "reframing_log"):
        content.pop(key, None)

    errors = []
    warnings = []
    auto_fixes = []

    # --- CHECK 1: Years in summary ---
    summary = (content.get("professional_summary") or "").strip()
    if summary:
        summary_lower = summary.lower()
        if "8+ years" not in summary_lower and "9+ years" not in summary_lower and "10+ years" not in summary_lower:
            has_lower_years = any(f"{n}+ years" in summary_lower for n in range(1, 8))
            if has_lower_years:
                errors.append({
                    "rule": "years_check",
                    "severity": "CRITICAL",
                    "message": "Summary must say '8+ years' or higher. Found lower year count.",
                })
            else:
                warnings.append({
                    "rule": "years_check",
                    "severity": "WARN",
                    "message": "Summary does not explicitly mention '8+ years'. Verify it opens correctly.",
                })

    # --- CHECK 2: Bullet count per role ---
    work = content.get("work_experience") or []
    for i, role in enumerate(work):
        bullets = role.get("bullets") or []
        n = len(bullets)
        title = role.get("title") or ""
        company = role.get("company") or ""
        label = f"{company} ({title})"

        if _is_internship(role):
            if n > 1:
                warnings.append({
                    "rule": "bullet_count",
                    "severity": "WARN",
                    "message": f"Internship '{label}' has {n} bullets (max 1).",
                })
        elif i == 0:  # most recent
            if n < 4:
                warnings.append({
                    "rule": "bullet_count",
                    "severity": "WARN",
                    "message": f"Most recent role '{label}' has {n} bullets (need 4-5).",
                })
            elif n > 5:
                errors.append({
                    "rule": "bullet_count",
                    "severity": "ERROR",
                    "message": f"Most recent role '{label}' has {n} bullets (max 5).",
                })
        elif i == 1:  # second role
            if n < 3:
                warnings.append({
                    "rule": "bullet_count",
                    "severity": "WARN",
                    "message": f"Second role '{label}' has {n} bullets (need 3-6).",
                })
            elif n > 6:
                errors.append({
                    "rule": "bullet_count",
                    "severity": "ERROR",
                    "message": f"Second role '{label}' has {n} bullets (max 6).",
                })
        elif i == 2:  # third role
            if n < 3:
                warnings.append({
                    "rule": "bullet_count",
                    "severity": "WARN",
                    "message": f"Third role '{label}' has {n} bullets (need 3-4).",
                })
            elif n > 5:
                errors.append({
                    "rule": "bullet_count",
                    "severity": "ERROR",
                    "message": f"Third role '{label}' has {n} bullets (max 5).",
                })
        else:  # older roles
            if n > 2 and not _is_internship(role):
                warnings.append({
                    "rule": "bullet_count",
                    "severity": "WARN",
                    "message": f"Older role '{label}' has {n} bullets (max 2).",
                })

    # --- CHECK 3: Bullet length (20-30 words) ---
    for role in work:
        for j, bullet in enumerate(role.get("bullets") or []):
            wc = _word_count(bullet)
            label = f"{role.get('company', '')} bullet {j+1}"
            if wc > 30:
                warnings.append({
                    "rule": "bullet_length",
                    "severity": "WARN",
                    "message": f"'{label}' is {wc} words (max 30).",
                })
            elif wc < 15:
                warnings.append({
                    "rule": "bullet_length",
                    "severity": "WARN",
                    "message": f"'{label}' is {wc} words (min ~15-20).",
                })

    # --- CHECK 4: Skills cap (<=25) ---
    skills = content.get("skills") or {}
    total_skills = (
        len(skills.get("technical") or [])
        + len(skills.get("methodologies") or [])
        + len(skills.get("domains") or [])
    )
    if total_skills > 25:
        warnings.append({
            "rule": "skills_cap",
            "severity": "WARN",
            "message": f"Skills section has {total_skills} terms (max 25).",
        })

    # --- CHECK 5: Location format (City, Country) ---
    locations = []
    for role in work:
        loc = (role.get("location") or "").strip()
        if loc:
            locations.append(loc)
    if locations:
        has_comma = [("," in loc) for loc in locations]
        if not all(has_comma):
            warnings.append({
                "rule": "location_format",
                "severity": "WARN",
                "message": f"Inconsistent location format. All should be 'City, Country'. Found: {locations}",
            })

    # --- CHECK 6: Verb variety ---
    verb_counts = {}
    for role in work:
        for bullet in role.get("bullets") or []:
            words = (bullet or "").strip().split()
            if words:
                verb = words[0].lower().rstrip(".,;:")
                verb_counts[verb] = verb_counts.get(verb, 0) + 1

    for verb, count in verb_counts.items():
        if count >= 3:
            warnings.append({
                "rule": "verb_variety",
                "severity": "WARN",
                "message": f"Opening verb '{verb}' used {count} times (max 2 recommended).",
            })

    # --- CHECK 7: Banned verbs ---
    for role in work:
        for j, bullet in enumerate(role.get("bullets") or []):
            bullet_lower = (bullet or "").strip().lower()
            for banned in BANNED_START_PHRASES:
                if bullet_lower.startswith(banned):
                    errors.append({
                        "rule": "banned_verb",
                        "severity": "ERROR",
                        "message": f"{role.get('company', '')} bullet {j+1} starts with banned phrase '{banned}'.",
                    })
                    break

    # --- CHECK 8: Tech anachronism ---
    for role in work:
        if not _role_end_before_june_2023(role):
            continue
        for j, bullet in enumerate(role.get("bullets") or []):
            bullet_lower = (bullet or "").lower()
            for term in PRE_2023_TECH_TERMS:
                if term in bullet_lower:
                    errors.append({
                        "rule": "tech_anachronism",
                        "severity": "ERROR",
                        "message": (
                            f"{role.get('company', '')} bullet {j+1} uses '{term}' "
                            f"but role ended before June 2023."
                        ),
                    })
                    break

    # --- CHECK 9: Page length estimate + auto-trim ---
    est_pages = _estimate_page_length(content)
    if est_pages > 2.0:
        warnings.append({
            "rule": "page_length",
            "severity": "WARN",
            "message": f"Estimated {est_pages:.1f} pages (max 2). Auto-trimming oldest roles.",
        })
        # Auto-trim: reduce oldest non-internship roles to 2 bullets, internships to 1
        trimmed = False
        for i in range(len(work) - 1, -1, -1):
            role = work[i]
            bullets = role.get("bullets") or []
            if _is_internship(role):
                max_b = 1
            elif i >= 3:
                max_b = 1
            elif i == 2:
                max_b = 2
            else:
                continue  # don't trim top 2 roles

            if len(bullets) > max_b:
                removed = bullets[max_b:]
                role["bullets"] = bullets[:max_b]
                trimmed = True
                auto_fixes.append({
                    "action": "trimmed_bullets",
                    "role": role.get("company", ""),
                    "removed_count": len(removed),
                    "kept": max_b,
                })

        if trimmed:
            est_pages = _estimate_page_length(content)

    # --- Determine status ---
    has_critical = any(e.get("severity") == "CRITICAL" for e in errors)
    if has_critical:
        status = "FAIL"
    elif errors:
        status = "WARN"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    logger.info(
        "Format validation: %s (%d errors, %d warnings, %d auto-fixes, est %.1f pages)",
        status, len(errors), len(warnings), len(auto_fixes), est_pages,
    )

    return {
        "validated_content": content,
        "format_validation": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "auto_fixes": auto_fixes,
            "estimated_pages": round(est_pages, 2),
        },
    }
