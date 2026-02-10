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

**Process:** Generate tailored resume content following these STRICT rules:

**REFRAMING RULES (NON-NEGOTIABLE):**

1. XYZ FORMULA: Every bullet = "Accomplished [X] as measured by [Y], by doing [Z]"
   - X = What you did (using JD terminology)
   - Y = Quantified result (number, %, $)
   - Z = How you did it

2. EXACT JD LANGUAGE: If JD says "stakeholder management," resume says "stakeholder management" — never a synonym

3. EVERY BULLET HAS A METRIC: If original has no metric, estimate a defensible one
   - "Managed a team" → "Led cross-functional team of 8 engineers and designers"
   - "Improved the product" → "Drove 25% improvement in NPS through research-informed iteration"

4. REFRAMING BOUNDARIES:
   - ✅ Change framing (customer lifecycle → CRM strategy)
   - ✅ Emphasize different aspects per JD
   - ✅ Use JD vocabulary for real work
   - ✅ Add reasonable metrics to unquantified work
   - ✅ Reorder bullets (most JD-relevant first)
   - ❌ Invent work you never did
   - ❌ Claim tools/technologies never used
   - ❌ Fabricate companies or roles

5. SEMANTIC CLUSTERING: Distribute related keywords across bullets
   - Don't repeat "data analysis" 5 times
   - Use: "data analysis" + "data-driven decisions" + "analytical insights"

6. PROFESSIONAL SUMMARY: 3-4 lines mirroring the JD's top requirements + user's best metric

7. SKILLS SECTION: Every P0 and P1 hard skill, exactly as written in JD

8. TONE: Senior PM writing about their own work — confident, specific, outcome-oriented. NOT robotic keyword stuffing.

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
- [ ] Step 1: Profile Knowledge Base builder (profile_builder.py)
- [ ] Step 2: JD Parser (jd_parser.py)
- [ ] Step 3: Profile Mapper (profile_mapper.py)
- [ ] Step 4: Reframing Engine (reframer.py)
- [ ] Step 5: Keyword Optimizer (keyword_optimizer.py)
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
