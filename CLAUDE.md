# PLACEMENT TEAM — Claude Code Briefing Document

## PROJECT OVERVIEW

We are building an AI-powered job application system called "Placement Team." It operates as 3 specialized agents working together to dramatically increase a Product Manager's shortlist-to-apply ratio from 0.4% to 5%+.

### The Three Agents
1. **Profile Manager** — Maintains a structured Profile Knowledge Base (PKB) of the user's entire career
2. **Job Researcher** — Finds, scores, and ranks relevant job opportunities
3. **Apply Manager** — Generates tailored ATS-optimized resumes, cover letters, and outreach materials

### Current Phase: PHASE 1 — Resume Engine
We are building the Profile Manager + Resume Generation capability of Apply Manager as a standalone tool.

### Phase 1 KRA
- Generated resumes must score 90%+ on ATS keyword matching
- Resumes must sound human and compelling (not keyword-stuffed)
- End-to-end JD-to-PDF time: under 2 minutes

---

## PHASE 1 ARCHITECTURE

### Pipeline Flow
```
INPUT: User's Profile Folder + Resume Template (one-time) + Job Description (per application)
  │
  ▼
[Step 1: JD Deep Parse] → Structured JD analysis with prioritized keywords
  │
  ▼
[Step 2: Profile KB Query] → Mapping matrix: JD requirements ↔ user's experience
  │
  ▼
[Step 3: Intelligent Reframing] → Tailored resume bullets using JD language
  │
  ▼
[Step 4: Keyword Density Optimization] → Ensure all critical keywords are covered
  │
  ▼
[Step 5: ATS Format Compliance] → Apply formatting rules
  │
  ▼
[Step 6: Self-Scoring] → Score resume, iterate if below 90
  │
  ▼
[Step 7: Final Output] → PDF + DOCX + Score Report + Reframing Log

OUTPUT: Tailored resume package in /output folder
```

### Folder Structure
```
placement-team/
├── CLAUDE.md                  # This file — project context
├── profile/                   # User's raw career documents
│   ├── resume.pdf
│   ├── linkedin.pdf
│   └── projects.md
├── templates/                 # Resume template
│   └── template.docx
├── engine/                    # Core code
│   ├── __init__.py
│   ├── jd_parser.py          # Step 1
│   ├── profile_builder.py    # Builds PKB from profile/ docs
│   ├── profile_mapper.py     # Step 2
│   ├── reframer.py           # Step 3 (MOST CRITICAL FILE)
│   ├── keyword_optimizer.py  # Step 4
│   ├── formatter.py          # Step 5
│   ├── scorer.py             # Step 6
│   └── generator.py          # Step 7
├── data/
│   └── pkb.json              # Profile Knowledge Base (generated)
├── output/                   # Generated resumes land here
├── tests/                    # Test JDs and validation
│   └── sample_jds/
├── requirements.txt
└── main.py                   # Orchestrator
```

---

## STEP-BY-STEP ALGORITHM SPECIFICATION

### STEP 1: JD DEEP PARSE (jd_parser.py)

**Input:** Job description text (pasted string or URL to scrape)

**Process:**
1. Accept JD as raw text or URL (if URL, scrape the page content)
2. Extract and categorize into these buckets:
   - hard_skills: specific tools, technologies, methodologies (e.g., "SQL", "A/B testing", "Agile")
   - soft_skills: leadership, communication, collaboration signals (e.g., "cross-functional leadership")
   - industry_terms: domain vocabulary (e.g., "SaaS", "CRM", "fintech", "marketplace")
   - experience_requirements: years, seniority, specific experiences (e.g., "5+ years PM experience")
   - education_requirements: degrees, certifications
   - key_responsibilities: what the role actually does day-to-day
   - achievement_language: what kind of results they want (e.g., "drove growth", "increased retention")
   - company_context: what the company does, team, stage
   - job_level: seniority signals (Senior, Lead, Director, IC)
   - cultural_signals: values and culture markers (e.g., "data-driven", "move fast")
3. Assign priority:
   - P0 (Must-Have): Keywords in title, first paragraph, or "Requirements" section
   - P1 (Should-Have): Keywords in description body, mentioned once
   - P2 (Nice-to-Have): Keywords in "Preferred" or "Bonus" sections
4. Extract the exact phrases as they appear in the JD (for ATS exact-match)

**Output:** JSON object:
```json
{
  "job_title": "Senior Product Manager, CRM Platform",
  "company": "Company Name",
  "location": "Remote / Dubai / London",
  "hard_skills": [{"skill": "SQL", "priority": "P0", "original_phrase": "proficiency in SQL"}],
  "soft_skills": [{"skill": "cross-functional leadership", "priority": "P0", "original_phrase": "lead cross-functional teams"}],
  "industry_terms": [{"term": "CRM", "priority": "P0"}],
  "experience_requirements": [{"requirement": "5+ years product management", "priority": "P0"}],
  "education_requirements": [],
  "key_responsibilities": ["Own the CRM platform roadmap", "Drive user retention strategy"],
  "achievement_language": ["drove growth", "increased retention", "reduced churn"],
  "company_context": "Series B SaaS startup in customer engagement space",
  "job_level": "Senior IC",
  "cultural_signals": ["data-driven", "customer-obsessed"],
  "all_keywords_flat": ["SQL", "CRM", "cross-functional", "retention", ...],
  "p0_keywords": ["SQL", "CRM", "product roadmap", ...],
  "p1_keywords": [...],
  "p2_keywords": [...]
}
```

---

### STEP 2: PROFILE KNOWLEDGE BASE BUILD & QUERY (profile_builder.py + profile_mapper.py)

**profile_builder.py — One-time setup**

**Input:** All files in /profile folder

**Process:**
1. Read all documents (PDF via pdfplumber, DOCX via python-docx, MD/TXT directly)
2. Extract every piece of career information exhaustively
3. Structure into PKB format

**Output:** data/pkb.json
```json
{
  "personal_info": {
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin_url": "",
    "portfolio_url": ""
  },
  "work_experience": [
    {
      "company": "Company Name",
      "title": "Product Manager",
      "dates": {"start": "Jan 2020", "end": "Mar 2023"},
      "duration_months": 38,
      "bullets": [
        {
          "original_text": "Led the development of customer lifecycle features",
          "skills_demonstrated": ["product management", "customer lifecycle", "feature development"],
          "tools_used": ["SQL", "Mixpanel", "Jira"],
          "metrics": ["35% retention improvement", "12-person team"],
          "domain": "fintech"
        }
      ],
      "industry": "fintech",
      "company_size": "Series B startup, 200 employees"
    }
  ],
  "skills": {
    "hard_skills": ["SQL", "A/B testing", "product analytics", "roadmapping"],
    "soft_skills": ["cross-functional leadership", "stakeholder management", "strategic thinking"],
    "tools": ["Jira", "Mixpanel", "Amplitude", "Figma", "Tableau"],
    "methodologies": ["Agile", "Scrum", "Design Thinking", "Jobs-to-be-Done"],
    "domains": ["fintech", "SaaS", "B2B", "payments"]
  },
  "education": [],
  "certifications": [],
  "projects": [],
  "achievements": [],
  "all_experience_keywords": []
}
```

**profile_mapper.py — Per JD**

**Input:** Parsed JD (from Step 1) + pkb.json

**Process:**
For each requirement in the JD, search PKB and classify:
- DIRECT: User has this exact skill/experience
- ADJACENT: User has something closely related that can be reframed
- TRANSFERABLE: User did this in a different context
- GAP: User doesn't have this

For ADJACENT and TRANSFERABLE, generate a reframing strategy.

**Output:** Mapping matrix JSON:
```json
{
  "mappings": [
    {
      "jd_requirement": "CRM platform strategy",
      "priority": "P0",
      "match_type": "ADJACENT",
      "source_experience": {
        "company": "FinTech Co",
        "bullet": "Led customer lifecycle management features",
        "skills": ["customer lifecycle", "retention"]
      },
      "reframe_strategy": "Reframe customer lifecycle management as CRM strategy work. Emphasize customer data platform, segmentation, and retention — core CRM functions.",
      "confidence": 0.8,
      "interview_defensible": true
    }
  ],
  "coverage_summary": {
    "p0_covered": 8,
    "p0_total": 10,
    "p0_coverage_pct": 80,
    "gaps": ["healthcare domain experience"]
  }
}
```

---

### STEP 3: INTELLIGENT REFRAMING ENGINE (reframer.py) — MOST CRITICAL

**Input:** Mapping matrix + PKB + JD analysis

**Process:** Generate tailored resume content following these STRICT rules. Every resume generated must follow all 13 rules. These are final and non-negotiable.

**RESUME CONTENT RULES (NON-NEGOTIABLE)**

**RULE 1: EXPERIENCE POSITIONING**
- Always present the user as "8+ years of experience" regardless of what the JD asks for
- Always position as "Senior Product Manager" in the professional summary
- Frame the user as an experienced senior leader in every resume

**RULE 2: PROFESSIONAL SUMMARY**
- Maximum 3 lines. No exceptions.
- Must open with: "Senior Product Manager with 8+ years..."
- Must contain the top 3 skills/requirements from the JD
- Must contain 2-3 of the user's strongest metrics (highest numbers like 2.5× adoption, 75% engagement lift, 50% revenue growth)
- Must reference the target company's domain language. If the company is in beauty/wellness, the summary should reference service-based businesses. If in fintech, reference financial platforms. Make the hiring manager feel "this person gets our business."
- No generic filler like "passionate" or "results-driven"
- The summary alone should make a recruiter think "I need to call this person"

**RULE 3: BULLET POINT STRUCTURE**
- XYZ formula: "Accomplished [X] as measured by [Y], by doing [Z]"
- Every bullet MUST contain a quantified metric (number, %, $, team size)
- Maximum 20-30 words per bullet. One clear point, one metric.
- Maximum 3-4 JD keywords per bullet. More = keyword stuffing.
- Lead each role with the most impressive and JD-relevant bullet
- BANNED starting words: "Responsible for", "Managed", "Helped", "Assisted", "Participated", "Planned"
- REQUIRED starting verbs: Led, Drove, Launched, Built, Owned, Delivered, Designed, Spearheaded, Achieved, Scaled, Transformed, Architected
- If a bullet describes a task instead of an outcome, rewrite it as an outcome
- Bad: "Managed the product roadmap for the CRM platform"
- Good: "Owned CRM platform strategy serving 30,000+ businesses, driving 35% improvement in customer retention"

**RULE 4: BULLETS PER ROLE**
- Most recent role: Maximum 4-5 bullets
- Second most recent role: Maximum 5 bullets
- Third role: Maximum 3-4 bullets
- Roles older than 5 years: Maximum 1-2 lines
- Internships: 1 line maximum. Remove entirely if not relevant.
- Early career / developer roles: 1 line showing technical background
- If a bullet has no metric and you cannot reasonably estimate one, CUT the bullet rather than keep it

**RULE 5: ONLY RELEVANT POINTERS**
- Every bullet must directly map to a P0 or P1 JD requirement
- Before including any bullet, ask: "Does this help get shortlisted for THIS specific job?"
- If the answer is no, cut it regardless of how impressive it sounds
- 4 perfect bullets beat 8 mediocre ones

**RULE 6: TOP 1% LANGUAGE**
- Every bullet should read like a top 1% PM wrote it — outcomes, not tasks
- Frame everything as business impact: revenue, growth, retention, efficiency, cost reduction, adoption
- Show strategic thinking: not just what was built, but WHY it mattered and the RESULT
- Think: "How would a VP describe this work in a board presentation?"
- Elevate scope: "Built chatbot" → "Launched AI-powered self-service automation reducing ticket volume by 35% and improving conversion by 25%"

**RULE 7: REFRAMING BOUNDARIES**
- ✅ Change framing of real experience to match JD language
- ✅ Emphasize different aspects of the same role for different JDs
- ✅ Use exact JD vocabulary for real work
- ✅ Add reasonable, defensible metrics to unquantified work
- ✅ Reorder bullets so most JD-relevant comes first
- ✅ Elevate strategic framing of real work
- ❌ Invent work that never happened
- ❌ Claim tools or technologies never used
- ❌ Do NOT say "LLM-powered" for products built before 2023 when LLMs were not mainstream. Use "NLP-driven" or "conversational AI" or "ML-powered" for pre-2023 work.
- ❌ Do NOT use the word "planned" for features — only reference shipped or deployed work

**RULE 8: KEYWORD USAGE**
- Use EXACT phrases from JD — not synonyms
- P0 keywords: appear 2-3 times across resume
- P1 keywords: appear 1-2 times
- No keyword more than 4 times total
- Include both full forms and abbreviations
- Distribute across summary + skills + experience — never cluster

**RULE 9: SKILLS SECTION**
- Maximum 25 terms total
- Organize into: Technical, Methodologies, Domains
- Every term must map to a P0 or P1 JD requirement
- Add domain terms that match the target company's business (e.g., if company does POS and CRM, include POS and CRM in domains)
- No filler skills

**RULE 10: RESUME LENGTH AND FORMAT**
- Maximum 2 pages, target 1.5 pages
- Output both PDF and DOCX
- Single column, no sidebars, no two-column
- Font: Arial or Calibri, 10-11pt body, 14-16pt name
- Standard headers: "Professional Summary", "Work Experience", "Skills", "Education", "Certifications"
- No graphics, tables, images, icons
- File naming: "FirstName_LastName_SeniorProductManager_Resume.pdf"

**RULE 11: EDUCATION AND CERTIFICATIONS**
- One line each for education
- All certifications on one line separated by pipes
- Only include certifications relevant to target JD

**RULE 12: TONE AND VOICE**
- Confident but not arrogant
- Sounds like a human, not AI. Read every bullet out loud.
- No buzzword chains
- Think: How would Shreyas Doshi describe this work?
- Crisp, direct, impactful

**RULE 13: SELF-CHECK BEFORE OUTPUT**
Before finalizing, verify ALL of these:
- Summary is 3 lines, opens with "Senior Product Manager with 8+ years"
- Summary references target company's domain/industry
- No role has more than 5 bullets
- Every bullet has a metric
- Every bullet is 20-30 words
- No bullet starts with banned verbs (Managed, Responsible for, Helped, Planned)
- No bullet has more than 4 JD keywords
- No pre-2023 work claims "LLM-powered"
- Total resume fits in 1.5-2 pages
- Skills section has ≤25 terms
- Every bullet passes "can I defend this in an interview?"
- Every bullet passes "does this sound like a top 1% PM?"
- Fidelity internship is 1 line max
- Cognizant developer role is 1 line max
- Reframing log is complete with interview prep notes

**Output:**
```json
{
  "professional_summary": "...",
  "work_experience": [
    {
      "company": "FinTech Co",
      "title": "Senior Product Manager",
      "dates": "Jan 2020 – Mar 2023",
      "bullets": [
        "Spearheaded CRM platform strategy driving 35% improvement in customer retention by designing segmentation-based lifecycle programs across 3 product verticals",
        "..."
      ]
    }
  ],
  "skills": {
    "technical": ["SQL", "A/B Testing", "Product Analytics", ...],
    "methodologies": ["Agile", "Scrum", ...],
    "domains": ["CRM", "SaaS", ...]
  },
  "education": [...],
  "certifications": [...],
  "reframing_log": [
    {
      "original": "Led customer lifecycle management features",
      "reframed": "Spearheaded CRM platform strategy driving 35% improvement in customer retention",
      "jd_keywords_used": ["CRM", "platform strategy", "retention"],
      "what_changed": "Reframed 'customer lifecycle' as 'CRM platform strategy' to match JD language. Added retention metric from actual project data.",
      "interview_prep": "Be ready to explain: You built customer lifecycle features that functioned as a CRM — segmentation, targeted campaigns, retention tracking. Frame it as CRM strategy."
    }
  ]
}
```

---

### STEP 4: KEYWORD DENSITY OPTIMIZATION (keyword_optimizer.py)

**Input:** Generated resume content + JD analysis

**Process:**
1. Count occurrences of every P0 keyword → must appear 2-3 times
2. Count P1 keywords → must appear 1-2 times
3. Check no keyword exceeds 4 occurrences (reduce if so)
4. Verify distribution across sections (summary + skills + experience)
5. Check both full forms and abbreviations (e.g., "Product Manager" AND "PM")
6. If keyword is missing, suggest natural insertion point

**Output:** Optimized content + keyword coverage report:
```json
{
  "optimized_content": {...},
  "keyword_report": {
    "p0_coverage": 95,
    "p1_coverage": 88,
    "missing_keywords": ["Tableau"],
    "over_used_keywords": [],
    "insertion_suggestions": [
      {"keyword": "Tableau", "suggested_location": "skills section or bullet about data analysis"}
    ]
  }
}
```

---

### STEP 5: ATS FORMAT COMPLIANCE (formatter.py)

**Input:** Optimized resume content

**ATS FORMAT RULES (MANDATORY — NO EXCEPTIONS):**
1. Single column layout. No two-column, no sidebars.
2. Reverse-chronological order within work experience.
3. Standard section headers EXACTLY as: "Professional Summary", "Work Experience", "Skills", "Education", "Certifications"
4. Fonts: Arial OR Calibri. Body: 10-11pt. Name: 14-16pt. Section headers: 12-13pt bold.
5. NO graphics, tables, text boxes, images, icons, logos, progress bars.
6. NO headers/footers for critical content (name/contact must be in body).
7. Standard round bullet points only.
8. Consistent date format throughout (e.g., "Jan 2020 – Mar 2023").
9. File naming: "FirstName_LastName_ProductManager_Resume.pdf"
10. Length: 1-2 pages max.
11. Margins: 0.5-1 inch all sides.
12. Output both PDF and DOCX.
13. Contact info at top: Name, Phone, Email, LinkedIn URL, Location.
14. No "References available upon request" or objective statements.

**Output:** Format-compliant content ready for rendering

---

### STEP 6: SELF-SCORING ENGINE (scorer.py)

**Input:** Final resume content + JD analysis

**Scoring Formula:**
```
TOTAL = (Keyword Match × 0.40) + (Semantic Alignment × 0.25) + 
        (Format Compliance × 0.15) + (Achievement Density × 0.10) + 
        (Human Readability × 0.10)
```

**Component Details:**
- Keyword Match (40%): (P0 found / P0 total) × 100. Penalty: -10 for each missing P0.
- Semantic Alignment (25%): Does resume narrative match JD intent? Score 0-100.
- Format Compliance (15%): (format rules passed / total rules) × 100. Target: 100%.
- Achievement Density (10%): (bullets with metrics / total bullets) × 100. Target: 80%+.
- Human Readability (10%): Natural flow? No robotic phrasing? No keyword soup? Score 0-100.

**Iteration Logic:**
- Score >= 90 → Output final resume
- Score 80-89 → One optimization pass on lowest component, re-score
- Score < 80 → Re-run Steps 3-5 with feedback, max 3 iterations
- After 3 iterations if still < 80 → Output with warning

**Output:** Score report JSON

---

### STEP 7: FINAL OUTPUT GENERATION (generator.py)

**Input:** Scored and approved resume content + template

**Output (saved to /output/[company_name]_[date]/):**
1. Resume PDF (ATS-optimized, using template)
2. Resume DOCX (same content, Word format)
3. score_report.json (all 5 scoring components + total)
4. keyword_coverage.json (which keywords covered, where)
5. reframing_log.json (what changed and why)
6. interview_prep.md (for each reframed bullet, how to discuss it)

---

## TECHNOLOGY STACK

- **Language:** Python 3.11+
- **PDF Reading:** pdfplumber
- **DOCX Generation:** python-docx
- **PDF Generation:** Convert DOCX to PDF via WeasyPrint or reportlab (or use python-docx + LibreOffice)
- **LLM:** Claude API (Anthropic) for intelligent reframing, scoring, analysis
- **Web Scraping (for JD URLs):** requests + BeautifulSoup
- **Job Scraping (Phase 2):** JobSpy library
- **Testing:** pytest

## DEPENDENCIES (requirements.txt)
```
anthropic
pdfplumber
python-docx
beautifulsoup4
requests
reportlab
pytest
```

---

## QUALITY STANDARDS

1. Every generated resume MUST score 90+ before being output
2. Every reframed bullet MUST have a corresponding interview prep note
3. The reframing log MUST be transparent — user should know exactly what changed
4. No keyword should appear more than 4 times in the resume
5. The professional summary MUST be customized per JD (never generic)
6. Skills section MUST mirror JD terminology exactly

---

## CURRENT BUILD STATUS

> **UPDATE THIS SECTION AS YOU PROGRESS**

- [x] Step 0: Project setup, folder structure, dependencies
- [x] Step 1: Profile Knowledge Base builder (profile_builder.py)
- [x] Step 2: JD Parser (jd_parser.py)
- [x] Step 3: Profile Mapper (profile_mapper.py)
- [x] Step 4: Reframing Engine (reframer.py)
- [x] Step 5: Keyword Optimizer (keyword_optimizer.py)
- [ ] Step 6: Scorer (scorer.py)
- [ ] Step 7: Formatter (formatter.py)
- [ ] Step 8: Generator (generator.py)
- [ ] Step 9: Orchestrator (main.py)
- [ ] Step 10: Testing with 5 real JDs
- [ ] Step 11: Iteration until 90+ consistent

---

## HOW TO BUILD (For Claude Code)

When building each step:
1. Read the full specification for that step in this document
2. Build the file with proper input/output interfaces
3. Include error handling and logging
4. Write a simple test that validates the output structure
5. After building, run the test and verify
6. Update the BUILD STATUS section above
7. Commit to git with a descriptive message

When I say "build step N" — refer to the specification above for that step and follow it exactly. Ask me for clarification if any specification is ambiguous. Do NOT skip steps or combine steps unless I explicitly ask.
