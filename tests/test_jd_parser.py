"""Tests for jd_parser.py — validates parsed JD structure and content."""

import json
import os

import pytest

PARSED_JD_PATH = "tests/sample_jds/zenoti_pm_parsed.json"


@pytest.fixture
def parsed_jd():
    """Load the parsed Zenoti JD. Skip if not generated yet."""
    if not os.path.exists(PARSED_JD_PATH):
        pytest.skip("Parsed JD not generated yet — run JD parser test first")
    with open(PARSED_JD_PATH, "r") as f:
        return json.load(f)


class TestJDStructure:
    """Validate the parsed JD has all required fields."""

    def test_has_job_title(self, parsed_jd):
        assert parsed_jd.get("job_title"), "job_title is missing"

    def test_has_company(self, parsed_jd):
        assert parsed_jd.get("company"), "company is missing"

    def test_has_hard_skills(self, parsed_jd):
        assert len(parsed_jd.get("hard_skills", [])) >= 5, "Too few hard_skills"

    def test_hard_skills_have_priority(self, parsed_jd):
        for skill in parsed_jd["hard_skills"]:
            assert "skill" in skill, "hard_skill entry missing 'skill' key"
            assert skill.get("priority") in ("P0", "P1", "P2"), (
                f"Invalid priority for {skill.get('skill')}"
            )

    def test_has_soft_skills(self, parsed_jd):
        assert len(parsed_jd.get("soft_skills", [])) >= 2, "Too few soft_skills"

    def test_has_industry_terms(self, parsed_jd):
        assert len(parsed_jd.get("industry_terms", [])) >= 3, "Too few industry_terms"

    def test_has_experience_requirements(self, parsed_jd):
        assert len(parsed_jd.get("experience_requirements", [])) >= 1

    def test_has_key_responsibilities(self, parsed_jd):
        assert len(parsed_jd.get("key_responsibilities", [])) >= 3

    def test_has_achievement_language(self, parsed_jd):
        assert len(parsed_jd.get("achievement_language", [])) >= 1

    def test_has_company_context(self, parsed_jd):
        assert parsed_jd.get("company_context"), "company_context is missing"

    def test_has_job_level(self, parsed_jd):
        assert parsed_jd.get("job_level"), "job_level is missing"

    def test_has_cultural_signals(self, parsed_jd):
        assert len(parsed_jd.get("cultural_signals", [])) >= 1

    def test_has_keyword_lists(self, parsed_jd):
        assert len(parsed_jd.get("all_keywords_flat", [])) >= 20
        assert len(parsed_jd.get("p0_keywords", [])) >= 5
        assert len(parsed_jd.get("p1_keywords", [])) >= 5


class TestJDContent:
    """Validate parsed JD captures key content from Zenoti posting."""

    def test_company_is_zenoti(self, parsed_jd):
        assert "Zenoti" in parsed_jd["company"]

    def test_title_is_product_manager(self, parsed_jd):
        assert "Product Manager" in parsed_jd["job_title"]

    def test_captures_ai_skills(self, parsed_jd):
        all_kw = " ".join(parsed_jd.get("all_keywords_flat", [])).lower()
        assert "ai" in all_kw or "artificial intelligence" in all_kw
        assert "llm" in all_kw or "llms" in all_kw

    def test_captures_saas(self, parsed_jd):
        all_kw = " ".join(parsed_jd.get("all_keywords_flat", [])).lower()
        assert "saas" in all_kw

    def test_captures_gtm(self, parsed_jd):
        all_kw = " ".join(parsed_jd.get("all_keywords_flat", [])).lower()
        assert "gtm" in all_kw

    def test_captures_crm(self, parsed_jd):
        all_kw = " ".join(parsed_jd.get("all_keywords_flat", [])).lower()
        assert "crm" in all_kw

    def test_captures_experience_years(self, parsed_jd):
        reqs = json.dumps(parsed_jd.get("experience_requirements", [])).lower()
        assert "4+" in reqs or "4 years" in reqs

    def test_mba_is_p2(self, parsed_jd):
        """MBA should be P2 since the JD says 'is a plus'."""
        p2 = " ".join(parsed_jd.get("p2_keywords", [])).lower()
        assert "mba" in p2
