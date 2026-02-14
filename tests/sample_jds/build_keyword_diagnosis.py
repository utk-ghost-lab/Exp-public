#!/usr/bin/env python3
"""Build keyword_diagnosis.json: P0/P1 found/missing, exact score math, insertion suggestions for missing P0."""
import json
import re
import os

def _count_keyword_occurrences(text: str, keyword: str) -> int:
    if not keyword or not text:
        return 0
    pattern = re.escape(keyword)
    return len(re.findall(pattern, text, flags=re.IGNORECASE))

def _get_resume_text_by_section(resume_content: dict) -> dict:
    sections = {"summary": "", "skills": "", "experience": ""}
    sections["summary"] = (resume_content.get("professional_summary") or "").strip()
    sk = resume_content.get("skills") or {}
    tech = " ".join(sk.get("technical") or [])
    meth = " ".join(sk.get("methodologies") or [])
    dom = " ".join(sk.get("domains") or [])
    sections["skills"] = f"{tech} {meth} {dom}".strip()
    bullets = []
    for role in resume_content.get("work_experience") or []:
        for b in role.get("bullets") or []:
            bullets.append(b or "")
    sections["experience"] = " ".join(bullets)
    return sections

def suggest_where_to_add(keyword: str, resume_content: dict) -> str:
    """Suggest where a missing P0 keyword could be added naturally (no stuffing)."""
    kw_lower = (keyword or "").lower()
    work = resume_content.get("work_experience") or []
    # Map keyword to best placement
    if "product judgment" in kw_lower or "judgment" in kw_lower:
        return "Professional summary (e.g. 'Strong product judgment with...') or a bullet about trade-offs/prioritization (e.g. Planful or ICICI bullet)"
    if "design" in kw_lower and "product" not in kw_lower:
        return "Skills (methodologies) or a bullet about solution design / UI or workflow design (e.g. Planful, ICICI)"
    if "execution" in kw_lower:
        return "Professional summary or Planful/Wealthy bullet (e.g. 'execution across web and mobile')"
    if "cost" in kw_lower:
        return "A bullet about AI/ML trade-offs, build vs buy, or resource prioritization (Planful or ICICI)"
    if "engineering" in kw_lower:
        return "Professional summary ('partnering with engineering') or a PM bullet about cross-functional work (Planful, Wealthy, ICICI)"
    if "launch" in kw_lower:
        return "Already theme in several bullets; add explicitly in summary ('idea to launch') or one more launch bullet"
    if "workflows" in kw_lower or "workflow" in kw_lower:
        return "Skills (domains or technical) or Planful/Wealthy/ICICI bullets (conversational workflows, omnichannel workflows)"
    if "gtm" in kw_lower:
        return "Professional summary or a launch bullet (e.g. 'GTM execution' or 'GTM narrative')"
    if "data" in kw_lower:
        return "Summary ('data-driven decisioning') or skills; already present in 'data-driven' — add 'data' explicitly in one bullet"
    # Default
    return "Skills section (technical/methodologies/domains per relevance) or a relevant experience bullet where the work was done"

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    parsed_path = os.path.join(base, "zenoti_pm_parsed.json")
    reframed_path = os.path.join(base, "zenoti_pm_reframed.json")
    with open(parsed_path) as f:
        parsed_jd = json.load(f)
    with open(reframed_path) as f:
        resume_content = json.load(f)

    content = {
        "professional_summary": resume_content.get("professional_summary", ""),
        "work_experience": resume_content.get("work_experience", []),
        "skills": resume_content.get("skills", {}),
    }
    sections = _get_resume_text_by_section(content)
    full_text = sections["summary"] + " " + sections["skills"] + " " + sections["experience"]

    p0_keywords = list(dict.fromkeys(k for k in (parsed_jd.get("p0_keywords") or []) if k))
    p1_keywords = list(dict.fromkeys(k for k in (parsed_jd.get("p1_keywords") or []) if k))
    p0_total = len(p0_keywords)
    p1_total = len(p1_keywords) if p1_keywords else 0

    p0_found_list = []
    p0_missing_list = []
    p0_per_keyword = []
    for kw in p0_keywords:
        in_summary = _count_keyword_occurrences(sections["summary"], kw) > 0
        in_skills = _count_keyword_occurrences(sections["skills"], kw) > 0
        in_exp = _count_keyword_occurrences(sections["experience"], kw) > 0
        total = _count_keyword_occurrences(full_text, kw)
        where = []
        if in_summary: where.append("summary")
        if in_skills: where.append("skills")
        if in_exp: where.append("experience")
        rec = {"keyword": kw, "count": total, "found": total >= 1, "in_sections": where}
        p0_per_keyword.append(rec)
        if total >= 1:
            p0_found_list.append(kw)
        else:
            p0_missing_list.append(kw)
    p0_covered = len(p0_found_list)

    p1_found_list = []
    p1_missing_list = []
    for kw in p1_keywords:
        total = _count_keyword_occurrences(full_text, kw)
        if total >= 1:
            p1_found_list.append(kw)
        else:
            p1_missing_list.append(kw)
    p1_covered = len(p1_found_list)

    # Exact formula (scorer): (P0_found/P0_total)*70 + (P1_found/P1_total)*30, then minus abbreviation penalty
    p0_total_1 = p0_total or 1
    p1_total_1 = p1_total or 1
    p0_pct = 100.0 * p0_covered / p0_total_1
    p1_pct = 100.0 * p1_covered / p1_total_1
    base_score = (p0_pct * 0.70) + (p1_pct * 0.30)
    # Abbreviation penalty (from scorer)
    ABBREVIATION_PAIRS = [
        ("CRM", "Customer Relationship Management"), ("PM", "Product Manager"),
        ("AI", "Artificial Intelligence"), ("ML", "Machine Learning"),
        ("GTM", "Go-to-Market"), ("SMB", "Small and Medium Business"), ("B2B", "Business to Business"),
        ("ROI", "Return on Investment"), ("SaaS", "Software as a Service"),
        ("LLM", "Large Language Model"), ("RAG", "Retrieval Augmented Generation"),
    ]
    jd_text = (parsed_jd.get("job_title") or "") + " " + " ".join(parsed_jd.get("all_keywords_flat") or [])
    resume_lower = full_text.lower()
    jd_lower = jd_text.lower()
    abbr_penalty = 0
    for abbr, full in ABBREVIATION_PAIRS:
        in_jd = abbr.lower() in jd_lower or full.lower() in jd_lower
        if not in_jd:
            continue
        in_resume_abbr = abbr.lower() in resume_lower
        in_resume_full = full.lower() in resume_lower
        if in_resume_abbr != in_resume_full:
            abbr_penalty += 3
    abbr_penalty = min(10, abbr_penalty)
    final_keyword_score = round(max(0, min(100, base_score - abbr_penalty)), 1)

    missing_p0_insertions = []
    for kw in p0_missing_list:
        suggestion = suggest_where_to_add(kw, resume_content)
        missing_p0_insertions.append({"keyword": kw, "where_to_add": suggestion})

    diagnosis = {
        "p0": {
            "total": p0_total,
            "keywords_list": p0_keywords,
            "found_count": p0_covered,
            "found_list": p0_found_list,
            "missing_count": len(p0_missing_list),
            "missing_list": p0_missing_list,
            "per_keyword": p0_per_keyword,
        },
        "p1": {
            "total": p1_total,
            "found_count": p1_covered,
            "found_list": p1_found_list,
            "missing_count": len(p1_missing_list),
            "missing_list": p1_missing_list,
        },
        "exact_math": {
            "formula": "(P0_found/P0_total × 70) + (P1_found/P1_total × 30) - abbreviation_penalty",
            "P0_found": p0_covered,
            "P0_total": p0_total,
            "P1_found": p1_covered,
            "P1_total": p1_total,
            "step1_P0_contribution": f"({p0_covered}/{p0_total}) × 70 = {round((p0_covered/p0_total_1)*70, 2)}",
            "step2_P1_contribution": f"({p1_covered}/{p1_total}) × 30 = {round((p1_covered/p1_total_1)*30, 2)}",
            "base_score_before_penalty": round(base_score, 2),
            "abbreviation_penalty": abbr_penalty,
            "final_keyword_match_score": final_keyword_score,
        },
        "missing_p0_where_to_add": missing_p0_insertions,
    }
    out_path = os.path.join(base, "keyword_diagnosis.json")
    with open(out_path, "w") as f:
        json.dump(diagnosis, f, indent=2)
    print("Saved", out_path)
    print("P0 total:", p0_total, "| P0 found:", p0_covered, "| P0 missing:", len(p0_missing_list))
    print("P1 total:", p1_total, "| P1 found:", p1_covered, "| P1 missing:", len(p1_missing_list))
    print("Final keyword_match score:", final_keyword_score)

if __name__ == "__main__":
    main()
