"""Load past human edits and build a USER PREFERENCES block for the reframer."""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = "data/human_edits.jsonl"
MAX_EVENTS = 20
MAX_EXAMPLES = 10
MAX_SNIPPET_WORDS = 50


def _truncate(s: str, max_words: int = MAX_SNIPPET_WORDS) -> str:
    if not s or not s.strip():
        return ""
    words = s.strip().split()
    if len(words) <= max_words:
        return s.strip()
    return " ".join(words[:max_words]) + " ..."


def load_recent_edits(log_path: str = None, max_events: int = MAX_EVENTS) -> list:
    """Read last max_events lines from human_edits.jsonl. Return list of edit records (newest last)."""
    path = log_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        DEFAULT_LOG_PATH,
    )
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Last N (most recent)
    return records[-max_events:] if len(records) > max_events else records


def edits_to_preferences_text(edits: list, max_examples: int = MAX_EXAMPLES) -> str:
    """Turn edit records into "User changed: [snippet] to [snippet]" lines.

    Diffs professional_summary and work_experience bullets. Truncates long snippets.
    """
    lines = []
    for rec in edits:
        before = rec.get("content_before") or {}
        after = rec.get("content_after") or {}
        # Summary diff
        s_before = (before.get("professional_summary") or "").strip()
        s_after = (after.get("professional_summary") or "").strip()
        if s_before != s_after and s_before and s_after:
            lines.append(
                "User changed summary: \"%s\" to \"%s\""
                % (_truncate(s_before), _truncate(s_after))
            )
        # Bullets: compare role-by-role, bullet-by-bullet
        work_before = before.get("work_experience") or []
        work_after = after.get("work_experience") or []
        for i, (r_b, r_a) in enumerate(zip(work_before, work_after)):
            company = (r_b.get("company") or r_a.get("company") or "Role %d" % (i + 1))
            bullets_b = r_b.get("bullets") or []
            bullets_a = r_a.get("bullets") or []
            for j, (b_b, b_a) in enumerate(zip(bullets_b, bullets_a)):
                b_b = (b_b or "").strip()
                b_a = (b_a or "").strip()
                if b_b != b_a and b_b and b_a:
                    lines.append(
                        "User changed bullet (%s): \"%s\" to \"%s\""
                        % (company, _truncate(b_b), _truncate(b_a))
                    )
        if len(lines) >= max_examples:
            break
    return "\n".join(lines[:max_examples]) if lines else ""


def get_user_preferences_block(
    log_path: str = None,
    max_events: int = MAX_EVENTS,
    max_examples: int = MAX_EXAMPLES,
) -> Optional[str]:
    """Load recent edits, build preferences text, return block for reframer or None if no edits."""
    edits = load_recent_edits(log_path=log_path, max_events=max_events)
    if not edits:
        return None
    text = edits_to_preferences_text(edits, max_examples=max_examples)
    if not text.strip():
        return None
    block = (
        "USER PREFERENCES (from your past corrections; apply when generating):\n"
        + text.strip()
        + "\n"
    )
    logger.info("Loaded %d edit(s) into user preferences block", len(edits))
    return block
