"""Pre-PDF review and edit: show resume JSON, let user edit in $EDITOR, record edits for learning."""

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

HUMAN_EDITS_LOG = "data/human_edits.jsonl"
PRE_GENERATION_EDIT_FILENAME = "pre_generation_edit.json"
MAX_SNIPPET_WORDS = 50


def _content_equal(a: dict, b: dict) -> bool:
    """Deep equality for resume content (ignoring reframing_log/rule13 for comparison)."""
    def norm(c):
        if not c:
            return {}
        return {k: v for k, v in c.items() if k not in ("reframing_log", "rule13_self_check")}
    return json.dumps(norm(a), sort_keys=True) == json.dumps(norm(b), sort_keys=True)


def _get_editor_cmd():
    """Return [cmd, ...] for the user's editor. Prefer EDITOR, then platform fallbacks."""
    editor = os.environ.get("EDITOR")
    if editor:
        return editor.strip().split()
    if sys.platform == "darwin":
        return ["open", "-e"]
    for cmd in ("nano", "vim", "vi"):
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=1)
            return [cmd]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return []


def _is_interactive():
    """True if stdin looks like a TTY (user can respond to prompts)."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def offer_edit_and_apply(
    resume_content: dict,
    parsed_jd: dict,
    output_dir: str,
    company_slug: str,
    date_str: str,
    editor_cmd: list = None,
) -> tuple:
    """Write resume JSON to a file, open user's editor, re-read and validate.

    Returns:
        (resume_content, edit_record | None)
        - If user skipped or made no changes: edit_record is None.
        - If user edited and saved valid JSON with changes: edit_record is dict with
          timestamp_utc, jd_context, content_before, content_after.
    """
    editor = editor_cmd or _get_editor_cmd()
    if not editor or not _is_interactive():
        logger.info("Review step skipped (no editor or non-interactive).")
        return resume_content, None

    content_before = json.loads(json.dumps(resume_content))

    # Write to a temp file so user can edit
    fd, path = tempfile.mkstemp(suffix=".json", prefix="resume_edit_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(resume_content, f, indent=2, ensure_ascii=False)
    except Exception:
        os.close(fd)
        os.unlink(path)
        raise

    print()
    print("Resume JSON written to:", path)
    print("Open it in your editor, save when done, then press Enter here to continue (or Enter now to skip edits).")
    try:
        subprocess.run(editor + [path], timeout=3600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Editor exited with error or timeout: %s", e)
    except Exception as e:
        logger.warning("Editor failed: %s", e)

    try:
        with open(path, "r", encoding="utf-8") as f:
            content_after = json.load(f)
    except json.JSONDecodeError as e:
        print("Invalid JSON after edit:", e)
        print("Using original content. Fix the file and run again if you wanted to apply edits.")
        os.unlink(path)
        return resume_content, None
    finally:
        if os.path.exists(path):
            os.unlink(path)

    if _content_equal(content_before, content_after):
        logger.info("No changes detected; using original content.")
        return resume_content, None

    jd_context = {
        "company": (parsed_jd.get("company") or "").strip() or "Not specified",
        "job_title": (parsed_jd.get("job_title") or "").strip(),
    }
    edit_record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "jd_context": jd_context,
        "content_before": content_before,
        "content_after": content_after,
    }
    return content_after, edit_record


def save_edit_record(edit_record: dict, output_folder_path: str) -> None:
    """Write pre_generation_edit.json into the output folder."""
    if not edit_record or not output_folder_path:
        return
    os.makedirs(output_folder_path, exist_ok=True)
    path = os.path.join(output_folder_path, PRE_GENERATION_EDIT_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edit_record, f, indent=2, ensure_ascii=False)
    logger.info("Edit record saved to %s", path)


def append_human_edit_log(edit_record: dict, log_path: str = None) -> None:
    """Append one JSON object per line to human_edits.jsonl."""
    if not edit_record:
        return
    path = log_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), HUMAN_EDITS_LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps({
        "timestamp_utc": edit_record.get("timestamp_utc", ""),
        "company": (edit_record.get("jd_context") or {}).get("company", ""),
        "job_title": (edit_record.get("jd_context") or {}).get("job_title", ""),
        "content_before": edit_record.get("content_before", {}),
        "content_after": edit_record.get("content_after", {}),
    }, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info("Edit appended to %s", path)
