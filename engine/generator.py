"""Step 7: Final Output Generation

Generates the complete resume package:
1. Resume PDF (ATS-optimized, using template)
2. Resume DOCX (same content, Word format)
3. score_report.json
4. keyword_coverage.json
5. reframing_log.json
6. interview_prep.md

Output saved to: output/{company_name}_{date}/

Input: Scored resume content + template
Output: Full resume package in output folder
"""

import json
import logging

logger = logging.getLogger(__name__)


def generate_output(
    resume_content: dict,
    score_report: dict,
    keyword_report: dict,
    reframing_log: list,
    parsed_jd: dict,
    template_path: str = "templates/template.docx",
    output_dir: str = "output",
) -> str:
    """Generate the final resume package.

    Args:
        resume_content: Final formatted resume content
        score_report: Scoring results
        keyword_report: Keyword coverage data
        reframing_log: Log of all reframing decisions
        parsed_jd: Original JD analysis (for company name)
        template_path: Path to DOCX template
        output_dir: Base output directory

    Returns:
        Path to the output folder
    """
    raise NotImplementedError("Step 7 not yet implemented")
