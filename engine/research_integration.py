"""Step 1.5: Company Research Integration

Runs company research and fit analysis between JD parsing (Step 1) and
profile mapping (Step 2). Produces a strategic research brief that helps
the mapper and reframer position the resume more effectively.

Pipeline:
  1. researcher.job_scorer.score_job() — fast local fit scoring
  2. researcher.company_analyzer.analyze_company() — web-based company signals
  3. Claude Sonnet synthesis — strategic brief for resume positioning

The entire step is non-fatal: if anything fails, research_brief = None
and the pipeline continues as before.
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum wall-clock time for the entire research step
RESEARCH_TIMEOUT_SECONDS = 60


def run_company_research(parsed_jd: dict, pkb: dict) -> Optional[dict]:
    """Run company research and return a strategic brief for resume positioning.

    Args:
        parsed_jd: Structured JD analysis from jd_parser (Step 1 output).
        pkb: Profile Knowledge Base dict.

    Returns:
        Research brief dict with keys: role_purpose, company_pain_points,
        competitive_edge, critical_gaps, bridge_strategy, emphasis_areas,
        hiring_mode, fit_score_result, company_analysis.
        Returns None if the entire step fails.
    """
    start = time.time()

    # --- Sub-step 1: Fit scoring (fast, local) ---
    fit_result = None
    try:
        from researcher.job_scorer import score_job
        fit_result = score_job(parsed_jd, pkb)
        logger.info(
            "Research sub-step 1: fit score = %.1f (%s)",
            fit_result.get("fit_score", 0),
            fit_result.get("recommendation", "?"),
        )
    except Exception as e:
        logger.warning("Research sub-step 1 (fit scoring) failed: %s", e)

    # --- Sub-step 2: Company analysis (web scraping, may fail) ---
    company_analysis = None
    company_name = (parsed_jd.get("company") or "").strip()
    if company_name and company_name.lower() not in ("", "not specified", "unknown", "company"):
        try:
            from researcher.company_analyzer import analyze_company, load_watchlist
            watchlist = load_watchlist()
            companies = watchlist.get("companies", {})
            # Find matching company config (case-insensitive key match)
            config = None
            for key, val in companies.items():
                if key.lower() == company_name.lower():
                    config = val
                    break
            if config is None:
                # No watchlist entry — use minimal config
                config = {"career_url": "", "name": company_name}

            elapsed = time.time() - start
            if elapsed < RESEARCH_TIMEOUT_SECONDS - 10:
                company_analysis = analyze_company(company_name, config)
                logger.info("Research sub-step 2: company analysis completed for '%s'", company_name)
            else:
                logger.info("Research sub-step 2: skipped (%.0fs elapsed, near timeout)", elapsed)
        except Exception as e:
            logger.warning("Research sub-step 2 (company analysis) failed: %s", e)
    else:
        logger.info("Research sub-step 2: skipped (no valid company name)")

    # --- Sub-step 3: Claude Haiku synthesis → strategic brief ---
    elapsed = time.time() - start
    if elapsed >= RESEARCH_TIMEOUT_SECONDS - 5:
        logger.warning("Research step near timeout (%.0fs). Returning raw results without brief.", elapsed)
        return _build_fallback_brief(fit_result, company_analysis)

    try:
        brief = _synthesize_brief(parsed_jd, pkb, fit_result, company_analysis)
        brief["fit_score_result"] = fit_result
        brief["company_analysis"] = company_analysis
        logger.info("Research step completed in %.1fs", time.time() - start)
        return brief
    except Exception as e:
        logger.warning("Research sub-step 3 (brief synthesis) failed: %s", e)
        return _build_fallback_brief(fit_result, company_analysis)


def _build_fallback_brief(fit_result: Optional[dict], company_analysis: Optional[dict]) -> Optional[dict]:
    """Build a minimal brief from raw results when Claude synthesis fails."""
    if not fit_result and not company_analysis:
        return None

    brief = {
        "role_purpose": None,
        "company_pain_points": None,
        "competitive_edge": None,
        "critical_gaps": (fit_result or {}).get("missing_critical_skills", []),
        "bridge_strategy": None,
        "emphasis_areas": None,
        "hiring_mode": "unknown",
        "fit_score_result": fit_result,
        "company_analysis": company_analysis,
    }

    # Infer hiring mode from company analysis
    if company_analysis:
        pm_count = company_analysis.get("pm_roles_30d", 0)
        if pm_count and pm_count >= 5:
            brief["hiring_mode"] = "scaling"
        elif pm_count and pm_count >= 1:
            brief["hiring_mode"] = "steady"

    return brief


def _synthesize_brief(
    parsed_jd: dict,
    pkb: dict,
    fit_result: Optional[dict],
    company_analysis: Optional[dict],
) -> dict:
    """Call Claude Haiku to synthesize a strategic research brief."""
    import anthropic
    from engine.api_utils import messages_create_with_retry

    client = anthropic.Anthropic()

    # Build context for the synthesis prompt
    jd_context = json.dumps({
        "job_title": parsed_jd.get("job_title"),
        "company": parsed_jd.get("company"),
        "company_context": parsed_jd.get("company_context"),
        "key_responsibilities": parsed_jd.get("key_responsibilities", []),
        "p0_keywords": parsed_jd.get("p0_keywords", []),
        "cultural_signals": parsed_jd.get("cultural_signals", []),
        "job_level": parsed_jd.get("job_level"),
    }, indent=2)

    fit_context = json.dumps(fit_result, indent=2) if fit_result else "Not available"

    company_context = "Not available"
    if company_analysis:
        company_context = json.dumps({
            k: v for k, v in company_analysis.items()
            if k in ("pm_roles_30d", "funding_signal", "hiring_signal",
                      "pm_roles_history", "name", "salary_range")
        }, indent=2)

    # Candidate strengths summary
    candidate_domains = list(pkb.get("skills", {}).get("domains", []))
    candidate_hard_skills = list(pkb.get("skills", {}).get("hard_skills", []))[:10]

    # Condensed work history: top 3 roles with top 3 bullets each
    work_history_lines = []
    for role in (pkb.get("work_experience") or [])[:3]:
        company = role.get("company", "Unknown")
        title = role.get("title", "Unknown")
        dates = role.get("dates", {})
        date_str = f"{dates.get('start', '?')} – {dates.get('end', '?')}" if isinstance(dates, dict) else str(dates)
        work_history_lines.append(f"  {title} @ {company} ({date_str})")
        for b in (role.get("bullets") or [])[:3]:
            text = (b.get("original_text") or "") if isinstance(b, dict) else str(b)
            metrics = b.get("metrics", []) if isinstance(b, dict) else []
            metric_str = f" [metrics: {', '.join(metrics)}]" if metrics else ""
            if text.strip():
                work_history_lines.append(f"    • {text.strip()}{metric_str}")
    work_history_block = "\n".join(work_history_lines) if work_history_lines else "Not available"

    prompt = f"""You are a strategic career advisor. Given a job description analysis, fit scoring results, company intelligence, and the candidate's work history, produce a concise strategic brief for resume positioning.

JOB DESCRIPTION:
{jd_context}

FIT SCORE ANALYSIS:
{fit_context}

COMPANY INTELLIGENCE:
{company_context}

CANDIDATE STRENGTHS:
- Domains: {candidate_domains}
- Key skills: {candidate_hard_skills}

CANDIDATE WORK HISTORY (top 3 roles with key bullets):
{work_history_block}

Return ONLY this JSON (no markdown, no explanation):
{{
  "role_purpose": "1-2 sentences: why this role exists and what problem it solves",
  "company_pain_points": "1-2 sentences: what challenges this company needs solved based on JD + company signals",
  "competitive_edge": "1-2 sentences: why this candidate is a good fit based on their strengths vs JD needs",
  "critical_gaps": ["list of 2-4 missing skills/experiences that matter most"],
  "bridge_strategy": "2-3 sentences: how to position gaps as adjacent strengths in the resume",
  "emphasis_areas": ["list of 3-5 specific themes/achievements to highlight in resume bullets"],
  "hiring_mode": "scaling / steady / unknown",
  "gap_to_bullet_mapping": [
    {{
      "gap": "the missing skill/experience from critical_gaps",
      "target_bullet": "exact text of a specific bullet from the candidate's work history above that can be reframed to address this gap",
      "reframe_instruction": "specific instruction: how to reframe that bullet to cover the gap",
      "fallback": "if no bullet exists, suggest where to naturally mention this keyword (e.g. skills section, summary)"
    }}
  ],
  "keyword_insertion_plan": [
    {{
      "keyword": "a missing P0 keyword not naturally present in candidate's experience",
      "target_location": "summary / skills / specific_role_bullet",
      "integration_phrase": "a natural 5-10 word phrase incorporating this keyword that fits the target location"
    }}
  ],
  "summary_hooks": ["3 specific phrases (5-8 words each) tailored for the 3-line professional summary, referencing the company's domain and pain points"]
}}"""

    message = messages_create_with_retry(
        client,
        model="claude-sonnet-4-5-20250929",
        max_tokens=2500,
        timeout=45.0,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # Strip markdown fences if present
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

    brief = json.loads(response_text)

    # Validate expected keys (original + new actionable fields)
    expected_keys = {"role_purpose", "company_pain_points", "competitive_edge",
                     "critical_gaps", "bridge_strategy", "emphasis_areas", "hiring_mode",
                     "gap_to_bullet_mapping", "keyword_insertion_plan", "summary_hooks"}
    for key in expected_keys:
        brief.setdefault(key, None)

    _validate_brief_schema(brief)

    return brief


def _validate_brief_schema(brief: dict) -> None:
    """Validate and sanitize the new actionable fields in the brief.

    Ensures gap_to_bullet_mapping, keyword_insertion_plan, and summary_hooks
    have the expected structure. Fixes or defaults malformed entries in-place.
    """
    # gap_to_bullet_mapping: list of dicts with gap, target_bullet, reframe_instruction
    gtbm = brief.get("gap_to_bullet_mapping")
    if isinstance(gtbm, list):
        valid = []
        for entry in gtbm:
            if isinstance(entry, dict) and entry.get("gap"):
                entry.setdefault("target_bullet", None)
                entry.setdefault("reframe_instruction", "")
                entry.setdefault("fallback", "")
                valid.append(entry)
        brief["gap_to_bullet_mapping"] = valid
    else:
        brief["gap_to_bullet_mapping"] = []

    # keyword_insertion_plan: list of dicts with keyword, target_location, integration_phrase
    kip = brief.get("keyword_insertion_plan")
    if isinstance(kip, list):
        valid = []
        for entry in kip:
            if isinstance(entry, dict) and entry.get("keyword"):
                entry.setdefault("target_location", "skills")
                entry.setdefault("integration_phrase", "")
                valid.append(entry)
        brief["keyword_insertion_plan"] = valid
    else:
        brief["keyword_insertion_plan"] = []

    # summary_hooks: list of 3 short phrases
    hooks = brief.get("summary_hooks")
    if isinstance(hooks, list):
        brief["summary_hooks"] = [str(h) for h in hooks if h][:3]
    else:
        brief["summary_hooks"] = []

    logger.info(
        "Brief schema validated: %d gap mappings, %d keyword insertions, %d summary hooks",
        len(brief["gap_to_bullet_mapping"]),
        len(brief["keyword_insertion_plan"]),
        len(brief["summary_hooks"]),
    )
