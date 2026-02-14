"""Tests for reframer.py — validates reframed resume structure and reframing rules."""

import json
import os

import pytest

# Paths relative to project root
PARSED_JD_PATH = "tests/sample_jds/zenoti_pm_parsed.json"
MAPPING_PATH = "tests/sample_jds/zenoti_pm_mapping.json"
PKB_PATH = "data/pkb.json"
OUTPUT_REFRAME_PATH = "tests/sample_jds/zenoti_pm_reframed.json"


def _load_json(path: str):
    with open(path, "r") as f:
        return json.load(f)


@pytest.fixture
def parsed_jd():
    if not os.path.exists(PARSED_JD_PATH):
        pytest.skip("Parsed JD not found — run JD parser first")
    return _load_json(PARSED_JD_PATH)


@pytest.fixture
def mapping_matrix():
    if not os.path.exists(MAPPING_PATH):
        pytest.skip("Mapping not found — run profile mapper first")
    return _load_json(MAPPING_PATH)


@pytest.fixture
def pkb():
    if not os.path.exists(PKB_PATH):
        pytest.skip("PKB not found — run profile builder first")
    return _load_json(PKB_PATH)


@pytest.fixture
def reframed(parsed_jd, mapping_matrix, pkb):
    """Use cached reframed output if present; otherwise run reframer (requires ANTHROPIC_API_KEY)."""
    if os.path.exists(OUTPUT_REFRAME_PATH):
        return _load_json(OUTPUT_REFRAME_PATH)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "No ANTHROPIC_API_KEY and no cached reframed output. "
            "Set API key and run once to generate tests/sample_jds/zenoti_pm_reframed.json"
        )
    from engine.reframer import reframe_experience
    out = reframe_experience(mapping_matrix, pkb, parsed_jd)
    # Cache for next run
    with open(OUTPUT_REFRAME_PATH, "w") as f:
        json.dump(out, f, indent=2)
    return out


class TestReframerStructure:
    """Validate reframer output has required structure."""

    def test_has_professional_summary(self, reframed):
        assert "professional_summary" in reframed
        summary = reframed["professional_summary"]
        assert isinstance(summary, str), "professional_summary must be a string"
        assert len(summary) >= 50, "professional_summary should be 3-4 lines (50+ chars)"

    def test_has_work_experience(self, reframed):
        assert "work_experience" in reframed
        work = reframed["work_experience"]
        assert isinstance(work, list), "work_experience must be a list"
        assert len(work) >= 1, "work_experience must have at least one role"

    def test_work_experience_roles_have_required_fields(self, reframed):
        for i, role in enumerate(reframed["work_experience"]):
            assert "company" in role, f"work_experience[{i}] missing company"
            assert "title" in role, f"work_experience[{i}] missing title"
            assert "dates" in role, f"work_experience[{i}] missing dates"
            assert "bullets" in role, f"work_experience[{i}] missing bullets"
            assert isinstance(role["bullets"], list), f"work_experience[{i}].bullets must be list"
            assert len(role["bullets"]) >= 1, f"work_experience[{i}] must have at least one bullet"

    def test_bullets_are_strings(self, reframed):
        for role in reframed["work_experience"]:
            for j, bullet in enumerate(role["bullets"]):
                assert isinstance(bullet, str), f"bullet must be string, got {type(bullet)}"
                assert len(bullet.strip()) > 0, "bullet must be non-empty"

    def test_has_skills_dict(self, reframed):
        assert "skills" in reframed
        sk = reframed["skills"]
        assert isinstance(sk, dict), "skills must be a dict"
        assert "technical" in sk, "skills must have 'technical'"
        assert isinstance(sk["technical"], list), "skills.technical must be a list"
        # Should have some skills from JD
        assert len(sk.get("technical", [])) >= 3, "expected at least 3 technical skills"

    def test_has_education_and_certifications(self, reframed):
        assert "education" in reframed
        assert "certifications" in reframed
        assert isinstance(reframed["education"], list)
        assert isinstance(reframed["certifications"], list)

    def test_has_reframing_log(self, reframed):
        assert "reframing_log" in reframed
        log = reframed["reframing_log"]
        assert isinstance(log, list), "reframing_log must be a list"
        assert len(log) >= 1, "reframing_log should have at least one entry (reframed bullets)"

    def test_reframing_log_entries_complete(self, reframed):
        for i, entry in enumerate(reframed["reframing_log"]):
            assert "original" in entry, f"reframing_log[{i}] missing 'original'"
            assert "reframed" in entry, f"reframing_log[{i}] missing 'reframed'"
            assert "jd_keywords_used" in entry, f"reframing_log[{i}] missing 'jd_keywords_used'"
            assert "what_changed" in entry, f"reframing_log[{i}] missing 'what_changed'"
            assert "interview_prep" in entry, f"reframing_log[{i}] missing 'interview_prep'"
            assert isinstance(entry["jd_keywords_used"], list)


class TestReframerContent:
    """Validate reframing rules are reflected in content."""

    def test_bullets_have_metrics(self, reframed):
        """Every bullet should contain a number or % (XYZ formula: Y = metric)."""
        for role in reframed["work_experience"]:
            for bullet in role["bullets"]:
                has_number = any(c.isdigit() for c in bullet)
                has_pct = "%" in bullet or "percent" in bullet.lower()
                assert has_number or has_pct, (
                    f"Bullet should have a metric (number or %): {bullet[:80]}..."
                )

    def test_skills_include_p0_keywords(self, reframed, parsed_jd):
        """Skills section should include some P0 keywords from JD."""
        p0 = set(k.lower() for k in parsed_jd.get("p0_keywords", [])[:15])
        technical = [s.lower() for s in reframed["skills"].get("technical", [])]
        combined = " ".join(technical).lower()
        matches = sum(1 for k in p0 if k in combined or any(k in t for t in technical))
        assert matches >= 2, (
            f"Expected at least 2 P0 keywords in skills; P0 sample: {list(p0)[:5]}"
        )

    def test_work_experience_matches_pkb_companies(self, reframed, pkb):
        """Companies in reframed output should be from PKB (no fabrication)."""
        pkb_companies = {w["company"] for w in pkb.get("work_experience", [])}
        for role in reframed["work_experience"]:
            assert role["company"] in pkb_companies, (
                f"Company '{role['company']}' not in PKB — do not fabricate companies"
            )

    def test_reverse_chronological_order(self, reframed, pkb):
        """Work experience should be reverse-chronological (most recent first)."""
        pkb_work = pkb.get("work_experience", [])
        if len(pkb_work) < 2:
            return
        order = [r["company"] for r in reframed["work_experience"]]
        pkb_order = [w["company"] for w in pkb_work]
        assert order == pkb_order, (
            "Reframed work experience should match PKB order (reverse-chronological)"
        )
