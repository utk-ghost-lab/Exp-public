# Fast Validation Report — Step 9
Date: 2026-02-13 10:07:54

## Side-by-Side Comparison

| Metric                    | Test 1: Microsoft_Office_AI_team | Test 2: Intuit |
|---------------------------|────────────────────────|─────────────────────────|
| Company                   | Microsoft_Office_AI_team | Intuit |
| Expected difficulty       | STRETCH                | SOLID MATCH             |
|                           |                        |                         |
| **Scoring (10 components)**|                       |                         |
| keyword_match (x0.25)     | 79.3 | 85.2 |
| semantic_alignment (x0.15)| 100 | 83.3 |
| parseability (x0.10)      | 100.0 | 91.7 |
| title_match (x0.10)       | 100.0 | 100.0 |
| impact (x0.12)            | 93.3 | 100.0 |
| brevity (x0.08)           | 100 | 100 |
| style (x0.08)             | 80 | 80 |
| narrative (x0.07)         | 80.0 | 80.0 |
| completeness (x0.03)      | 80.0 | 80.0 |
| anti_pattern (x0.02)      | 100.0 | 100.0 |
| **TOTAL**                 | **90.4** | **89.4** |
|                           |                        |                         |
| **Keyword Coverage**      |                        |                         |
| P0 coverage               | 90.9% (10/11) | 100.0% (15/15) |
| P1 coverage               | 82.1% (23/28) | 60.5% (23/38) |
| Missing keywords           | 6 | 15 |
| Over-used keywords          | 0 | 4 |
|                           |                        |                         |
| **Output Quality**        |                        |                         |
| Iterations needed          | 3 | 3 |
| Format warnings            | 0 | 0 |
| Format errors              | 0 | 0 |
| Fit confidence             | HIGH | HIGH |

## What We Expect

**Test 1 (Microsoft_Office_AI_team) — STRETCH:**
- Role requires 10+ years and 3+ years people management
- Deep FP&A domain — Planful experience is relevant but may not be deep FP&A
- GPM/Principal level is a step up from Senior PM
- Expected score: 72-82. If higher, check whether reframer over-stretched.

**Test 2 (Intuit) — SOLID MATCH:**
- 5-8+ years PM experience (candidate has 8+)
- AI/ML concepts (Planful AI roadmap, Alexa bot, LLM prototyping)
- Product Led Growth (Planful web adoption 2.5x, Wealthy engagement 75%)
- Expected score: 85-92. If lower, something is broken.

## Gate Checks

### GATE 1: Did both run without crashing?
- [x] Test 1 completed successfully
- [x] Test 2 completed successfully

### GATE 2: Are the scores in the right ballpark?
- [ ] Test 1 (Intuit STRETCH): Score 90.4 — target 70-85
- [x] Test 2 (Microsoft MATCH): Score 89.4 — target 85-95
- [ ] Test 2 scores HIGHER than Test 1

### GATE 5: Anti-pattern check
- Weakest component Test 1: keyword_match
- Weakest component Test 2: style

## Missing P0 Keywords

**Test 1:** ["Bachelor's Degree", '5+ years experience in product/service/program management or software development', '8+ years experience in product/service/program management or software development', '2+ years experience taking a product, feature, or experience to market', '4+ years experience improving product metrics for a product, feature, or experience in a market', '4+ years experience disrupting a market for a product, feature, or experience']
**Test 2:** ['10+ years of product management experience', 'Deep domain experience building core financial products and solutions like FP&A', 'strong consulting background in FP&A domain', 'Led transformation at an enterprise level for large organizations', 'track record of mentoring and developing high performing individuals', 'coach', 'teach', 'problem solver', 'drive change', 'enterprise level']

## VERDICT

REVIEW NEEDED — see gate checks above
