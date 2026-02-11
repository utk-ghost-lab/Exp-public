"""Tests for profile_mapper.py — validates mapping matrix structure and content."""

import json
import os

import pytest

MAPPING_PATH = "tests/sample_jds/zenoti_pm_mapping.json"


@pytest.fixture
def mapping():
    """Load the generated mapping. Skip if not generated yet."""
    if not os.path.exists(MAPPING_PATH):
        pytest.skip("Mapping not generated yet — run profile mapper test first")
    with open(MAPPING_PATH, "r") as f:
        return json.load(f)


class TestMappingStructure:
    """Validate mapping matrix has all required fields."""

    def test_has_mappings(self, mapping):
        assert "mappings" in mapping
        assert len(mapping["mappings"]) >= 10, "Expected at least 10 mappings"

    def test_mappings_have_required_fields(self, mapping):
        for i, m in enumerate(mapping["mappings"]):
            assert m.get("jd_requirement"), f"mapping[{i}] missing jd_requirement"
            assert m.get("priority") in ("P0", "P1", "P2"), f"mapping[{i}] invalid priority"
            assert m.get("match_type") in ("DIRECT", "ADJACENT", "TRANSFERABLE", "GAP"), (
                f"mapping[{i}] invalid match_type: {m.get('match_type')}"
            )

    def test_non_gap_mappings_have_source(self, mapping):
        for m in mapping["mappings"]:
            if m["match_type"] != "GAP":
                assert m.get("source_experience"), (
                    f"{m['jd_requirement']} is {m['match_type']} but has no source_experience"
                )

    def test_adjacent_transferable_have_reframe_strategy(self, mapping):
        for m in mapping["mappings"]:
            if m["match_type"] in ("ADJACENT", "TRANSFERABLE"):
                assert m.get("reframe_strategy"), (
                    f"{m['jd_requirement']} is {m['match_type']} but has no reframe_strategy"
                )

    def test_has_confidence_scores(self, mapping):
        for m in mapping["mappings"]:
            if m["match_type"] != "GAP":
                assert "confidence" in m, f"{m['jd_requirement']} missing confidence"
                assert 0 <= m["confidence"] <= 1, f"{m['jd_requirement']} confidence out of range"

    def test_has_coverage_summary(self, mapping):
        assert "coverage_summary" in mapping
        summary = mapping["coverage_summary"]
        assert "p0_covered" in summary
        assert "p0_total" in summary
        assert "p0_coverage_pct" in summary
        assert "gaps" in summary

    def test_coverage_percentages_valid(self, mapping):
        summary = mapping["coverage_summary"]
        assert 0 <= summary["p0_coverage_pct"] <= 100
        assert summary["p0_covered"] <= summary["p0_total"]


class TestMappingContent:
    """Validate mapping content quality for Zenoti JD + Utkarsh's PKB."""

    def test_has_direct_matches(self, mapping):
        direct = [m for m in mapping["mappings"] if m["match_type"] == "DIRECT"]
        assert len(direct) >= 5, f"Only {len(direct)} DIRECT matches, expected at least 5"

    def test_has_adjacent_or_transferable(self, mapping):
        reframeable = [m for m in mapping["mappings"] if m["match_type"] in ("ADJACENT", "TRANSFERABLE")]
        assert len(reframeable) >= 2, f"Only {len(reframeable)} reframeable matches"

    def test_p0_coverage_above_60(self, mapping):
        pct = mapping["coverage_summary"]["p0_coverage_pct"]
        assert pct >= 60, f"P0 coverage too low: {pct}%"

    def test_gaps_identified(self, mapping):
        gaps = mapping["coverage_summary"]["gaps"]
        assert isinstance(gaps, list), "gaps should be a list"

    def test_beauty_wellness_is_gap_or_transferable(self, mapping):
        """Utkarsh doesn't have beauty/wellness experience — should be GAP or TRANSFERABLE."""
        beauty_mappings = [
            m for m in mapping["mappings"]
            if any(term in m["jd_requirement"].lower() for term in ["beauty", "wellness", "salon", "spa"])
        ]
        if beauty_mappings:
            for m in beauty_mappings:
                assert m["match_type"] in ("GAP", "TRANSFERABLE"), (
                    f"'{m['jd_requirement']}' should be GAP or TRANSFERABLE, got {m['match_type']}"
                )

    def test_ai_skills_are_direct(self, mapping):
        """Utkarsh has strong AI experience — AI keywords should be DIRECT."""
        ai_mappings = [
            m for m in mapping["mappings"]
            if any(term in m["jd_requirement"].lower() for term in ["ai", "llm", "ml", "genai"])
            and m["priority"] == "P0"
        ]
        direct_ai = [m for m in ai_mappings if m["match_type"] == "DIRECT"]
        assert len(direct_ai) >= 2, f"Only {len(direct_ai)} DIRECT AI matches, expected at least 2"

    def test_product_management_is_direct(self, mapping):
        """PM experience should be a strong DIRECT match."""
        pm_mappings = [
            m for m in mapping["mappings"]
            if "product manager" in m["jd_requirement"].lower()
        ]
        assert any(m["match_type"] == "DIRECT" for m in pm_mappings), "Product Manager should be DIRECT"
