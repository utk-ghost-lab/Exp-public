"""Cache for parsed JD and profile mapping to skip Steps 1 and 2 on repeat runs.

Cache key for parsed_jd: hash(jd_text).
Cache key for mapping: hash(jd_text) + pkb_version (mtime of data/pkb.json).
Invalidates mapping when PKB is rebuilt.
"""

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

CACHE_DIR = "data/cache"


def _jd_hash(jd_text: str) -> str:
    """Stable hash of normalized JD text."""
    normalized = (jd_text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _pkb_version(pkb_path: str) -> str:
    """Version string for PKB so mapping cache invalidates when PKB changes."""
    if not pkb_path or not os.path.exists(pkb_path):
        return "none"
    try:
        mtime = os.path.getmtime(pkb_path)
        return str(int(mtime))
    except OSError:
        return "unknown"


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def get_cached_parsed_jd(jd_text: str):
    """Return cached parsed_jd dict if present and valid, else None."""
    key = _jd_hash(jd_text)
    path = os.path.join(CACHE_DIR, f"parsed_jd_{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        logger.info("Using cached parsed JD (key=%s)", key)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Cache read failed for parsed_jd %s: %s", key, e)
        return None


def set_cached_parsed_jd(jd_text: str, parsed_jd: dict) -> None:
    """Write parsed_jd to cache."""
    _ensure_cache_dir()
    key = _jd_hash(jd_text)
    path = os.path.join(CACHE_DIR, f"parsed_jd_{key}.json")
    try:
        with open(path, "w") as f:
            json.dump(parsed_jd, f, indent=2)
        logger.debug("Cached parsed JD to %s", path)
    except OSError as e:
        logger.warning("Cache write failed for parsed_jd %s: %s", key, e)


def get_cached_mapping(jd_text: str, pkb_path: str):
    """Return cached mapping dict if present and PKB version matches, else None."""
    jkey = _jd_hash(jd_text)
    pver = _pkb_version(pkb_path)
    path = os.path.join(CACHE_DIR, f"mapping_{jkey}_{pver}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        logger.info("Using cached mapping (jd=%s, pkb=%s)", jkey, pver)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Cache read failed for mapping %s: %s", path, e)
        return None


def set_cached_mapping(jd_text: str, pkb_path: str, mapping: dict) -> None:
    """Write mapping to cache (keyed by JD hash and PKB version)."""
    _ensure_cache_dir()
    jkey = _jd_hash(jd_text)
    pver = _pkb_version(pkb_path)
    path = os.path.join(CACHE_DIR, f"mapping_{jkey}_{pver}.json")
    try:
        with open(path, "w") as f:
            json.dump(mapping, f, indent=2)
        logger.debug("Cached mapping to %s", path)
    except OSError as e:
        logger.warning("Cache write failed for mapping: %s", e)
