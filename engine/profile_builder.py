"""Step 2a: Profile Knowledge Base Builder

One-time setup: reads all documents in profile/ and builds a structured PKB.

Input: All files in profile/ folder (PDF, DOCX, MD, TXT)
Output: data/pkb.json
"""

import json
import logging

logger = logging.getLogger(__name__)


def build_pkb(profile_dir: str = "profile", output_path: str = "data/pkb.json") -> dict:
    """Build the Profile Knowledge Base from career documents.

    Args:
        profile_dir: Path to directory containing career documents
        output_path: Where to save the generated PKB JSON

    Returns:
        The PKB dict
    """
    raise NotImplementedError("Step 2a not yet implemented")
