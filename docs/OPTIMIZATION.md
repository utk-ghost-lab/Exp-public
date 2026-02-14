# Pipeline time optimizations

## Time breakdown (before changes)

- **Step 1 (JD parse):** ~15 s — already uses Haiku.
- **Step 2 (Profile mapper):** ~55 s — was Sonnet, 90 s timeout.
- **Step 3 (Reframer):** ~3–4 min — Sonnet, 120 s timeout; large payload often caused timeouts/retries.
- **Step 6 (Scoring + patch):** ~20–40 s — up to 2 patch reframe calls.

Total: ~5–6 minutes per run.

---

## Changes applied

1. **Slimmer JD payload for reframer**  
   Reframer now receives only: `job_title`, `company`, `location`, `key_responsibilities`, `achievement_language`, `company_context`, `job_level`, `p0_keywords`, `p1_keywords`.  
   Dropped: `hard_skills`, `soft_skills`, `industry_terms`, `experience_requirements`, `cultural_signals`, `p2_keywords` (keywords are already in p0/p1).  
   **Effect:** Smaller input → faster processing and fewer timeouts.

2. **Cap condensed PKB bullets (reframer)**  
   `_condensed_pkb_for_api()` now sends at most **5 bullets per role** (constant `MAX_BULLETS_PER_ROLE_FOR_API`).  
   **Effect:** Less payload; programmatic fixes still use full PKB for post-processing.

3. **Reframer timeout: 120 s → 180 s**  
   **Effect:** Fewer retries on large JDs; one successful call instead of timeout + retry saves ~2+ minutes.

4. **Profile mapper: Sonnet → Haiku**  
   Mapper uses `claude-haiku-4-5-20251001` with 60 s timeout (same as JD parser).  
   **Effect:** Step 2 typically ~20–35 s faster. Mapping is structured; quality should remain good. If you see weaker mappings, we can switch back to Sonnet for this step only.

---

## Expected impact

- **Step 2:** ~55 s → ~25–35 s.
- **Step 3:** Fewer retries; single call more likely to finish within 180 s.
- **Overall:** Target **~3–4.5 minutes** per run without sacrificing resume quality.

---

## Super-fast options (implemented)

5. **Cache parsed JD and mapping** ([engine/jd_cache.py](engine/jd_cache.py))  
   Parsed JD keyed by `hash(jd_text)`; mapping by JD hash + PKB version (mtime of `data/pkb.json`). On re-run with same JD and PKB, Steps 1 and 2 are skipped. Use `--no-cache` to disable.  
   **Effect:** Repeat runs save ~40–50 s.

6. **`--fast`**  
   At most one scoring iteration (one patch reframe if score &lt; 90). Saves ~20 s when score is 80–89.

7. **`--fast-no-improve`**  
   Score once and output without any patch improvement. Fastest; final score may be 78–88.

8. **Haiku for Step 6 patch reframe** ([engine/reframer.py](engine/reframer.py))  
   Patch reframe uses `claude-haiku-4-5-20251001` by default. Set `REFRAMER_PATCH_MODEL=sonnet` to use Sonnet.  
   **Effect:** ~10–15 s faster per patch call.

9. **`--combined-parse-map`**  
   Run JD parse and profile mapping in a single API call ([engine/jd_parse_and_map.py](engine/jd_parse_and_map.py)). Saves one round trip (~15–30 s). Combined result is cached when cache is enabled.

---

## Reverting mapper to Sonnet

If mapping quality drops with Haiku, in `engine/profile_mapper.py` change:

```python
model="claude-haiku-4-5-20251001",
timeout=60.0,
```

back to:

```python
model="claude-sonnet-4-5-20250929",
timeout=90.0,
```

---

## Reverting patch reframe to Sonnet

Patch reframe (Step 6) uses Haiku by default. To use Sonnet for patch improvement, set:

```bash
export REFRAMER_PATCH_MODEL=sonnet
```
