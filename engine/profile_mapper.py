"""Step 2b: Profile Mapper

Maps JD requirements to user experience from the PKB.
Classifies each mapping as DIRECT, ADJACENT, TRANSFERABLE, or GAP.

Input: Parsed JD (from Step 1) + pkb.json
Output: Mapping matrix with reframe strategies and coverage summary
"""

import json
import logging

logger = logging.getLogger(__name__)


def map_profile_to_jd(parsed_jd: dict, pkb: dict) -> dict:
    """Map JD requirements to user's experience in PKB.

    Args:
        parsed_jd: Structured JD analysis from jd_parser
        pkb: Profile Knowledge Base dict

    Returns:
        Mapping matrix with match types, reframe strategies, and coverage
    """
    raise NotImplementedError("Step 2b not yet implemented")
