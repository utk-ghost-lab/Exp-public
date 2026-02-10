"""Tests for profile_builder.py — validates PKB structure and content."""

import json
import os

import pytest

PKB_PATH = "data/pkb.json"


@pytest.fixture
def pkb():
    """Load the generated PKB. Skip if it doesn't exist yet."""
    if not os.path.exists(PKB_PATH):
        pytest.skip("PKB not generated yet — run 'python main.py --build-profile' first")
    with open(PKB_PATH, "r") as f:
        return json.load(f)


class TestPKBStructure:
    """Validate the PKB has all required top-level fields and structure."""

    def test_has_personal_info(self, pkb):
        assert "personal_info" in pkb
        info = pkb["personal_info"]
        assert info.get("name"), "name is missing"
        assert info.get("email"), "email is missing"
        assert info.get("phone"), "phone is missing"
        assert info.get("location"), "location is missing"

    def test_has_work_experience(self, pkb):
        assert "work_experience" in pkb
        assert len(pkb["work_experience"]) >= 3, "Expected at least 3 work experiences"

    def test_work_experience_fields(self, pkb):
        for i, exp in enumerate(pkb["work_experience"]):
            assert exp.get("company"), f"work_experience[{i}] missing company"
            assert exp.get("title"), f"work_experience[{i}] missing title"
            assert exp.get("dates"), f"work_experience[{i}] missing dates"
            assert exp.get("bullets"), f"work_experience[{i}] missing bullets"
            assert len(exp["bullets"]) >= 1, f"work_experience[{i}] has no bullets"

    def test_bullets_have_detail(self, pkb):
        for exp in pkb["work_experience"]:
            for j, bullet in enumerate(exp["bullets"]):
                assert bullet.get("original_text"), (
                    f"{exp['company']} bullet[{j}] missing original_text"
                )
                assert bullet.get("skills_demonstrated"), (
                    f"{exp['company']} bullet[{j}] missing skills_demonstrated"
                )

    def test_has_skills(self, pkb):
        assert "skills" in pkb
        skills = pkb["skills"]
        assert len(skills.get("hard_skills", [])) >= 3, "Too few hard_skills"
        assert len(skills.get("soft_skills", [])) >= 1, "Too few soft_skills"
        assert len(skills.get("tools", [])) >= 1, "Too few tools"
        assert len(skills.get("methodologies", [])) >= 1, "Too few methodologies"
        assert len(skills.get("domains", [])) >= 1, "Too few domains"

    def test_has_education(self, pkb):
        assert "education" in pkb
        assert len(pkb["education"]) >= 2, "Expected at least 2 education entries"

    def test_has_certifications(self, pkb):
        assert "certifications" in pkb
        assert len(pkb["certifications"]) >= 1, "Expected at least 1 certification"

    def test_has_achievements(self, pkb):
        assert "achievements" in pkb
        assert len(pkb["achievements"]) >= 1, "Expected at least 1 achievement"

    def test_has_all_experience_keywords(self, pkb):
        assert "all_experience_keywords" in pkb
        keywords = pkb["all_experience_keywords"]
        assert len(keywords) >= 20, f"Too few keywords ({len(keywords)}), expected 20+"


class TestPKBContent:
    """Validate PKB captures known content from Utkarsh's profile."""

    def test_name_is_correct(self, pkb):
        assert "Utkarsh" in pkb["personal_info"]["name"]
        assert "Tiwari" in pkb["personal_info"]["name"]

    def test_has_planful(self, pkb):
        companies = [exp["company"] for exp in pkb["work_experience"]]
        assert any("Planful" in c for c in companies), "Missing Planful"

    def test_has_wealthy(self, pkb):
        companies = [exp["company"] for exp in pkb["work_experience"]]
        assert any("Wealthy" in c for c in companies), "Missing Wealthy"

    def test_has_icici(self, pkb):
        companies = [exp["company"] for exp in pkb["work_experience"]]
        assert any("ICICI" in c for c in companies), "Missing ICICI Prudential"

    def test_has_cognizant(self, pkb):
        companies = [exp["company"] for exp in pkb["work_experience"]]
        assert any("Cognizant" in c for c in companies), "Missing Cognizant"

    def test_has_key_metrics(self, pkb):
        """Check that important metrics were captured across all bullets."""
        all_text = json.dumps(pkb["work_experience"]).lower()
        key_metrics = ["60%", "2.5", "75%", "50%", "250%", "30%", "25%"]
        found = [m for m in key_metrics if m in all_text]
        assert len(found) >= 4, f"Only found {len(found)} of {len(key_metrics)} key metrics: {found}"

    def test_has_insead(self, pkb):
        institutions = [e.get("institution", "") for e in pkb["education"]]
        assert any("INSEAD" in i for i in institutions), "Missing INSEAD"

    def test_has_iim(self, pkb):
        institutions = [e.get("institution", "") for e in pkb["education"]]
        assert any("IIM" in i or "Indian Institute of Management" in i for i in institutions), "Missing IIM Shillong"

    def test_keywords_include_core_skills(self, pkb):
        keywords_lower = [k.lower() for k in pkb["all_experience_keywords"]]
        keywords_str = " ".join(keywords_lower)
        core = ["sql", "agile", "fintech", "product"]
        for skill in core:
            assert skill in keywords_str, f"Core skill '{skill}' missing from keywords"
