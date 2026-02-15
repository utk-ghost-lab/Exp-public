"""
Phase 2 Component 1: Multi-Source Job Discovery

Searches multiple job boards, deduplicates results, and returns raw job listings.
Sources: Indeed (via JobSpy), Google Jobs scraping, company career pages, Naukri, Bayt.
"""

import hashlib
import json
import logging
import os
import re
import string
import sys
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SEEN_JOBS_PATH = os.path.join(DATA_DIR, "seen_jobs.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

REQUEST_TIMEOUT = 15


# ===================================================================
# Dedup helpers
# ===================================================================

def _url_hash(url: str) -> str:
    """SHA256 hash of URL, first 16 chars."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def load_seen_jobs() -> dict:
    """Load seen jobs cache from disk."""
    if os.path.exists(SEEN_JOBS_PATH):
        try:
            with open(SEEN_JOBS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_seen_jobs(seen: dict):
    """Persist seen jobs cache to disk."""
    os.makedirs(os.path.dirname(SEEN_JOBS_PATH), exist_ok=True)
    with open(SEEN_JOBS_PATH, "w") as f:
        json.dump(seen, f, indent=2)


def _dedup_jobs(jobs: list, seen: dict) -> list:
    """Remove jobs already in seen cache. Returns new jobs only."""
    new_jobs = []
    for job in jobs:
        url = job.get("job_url", "")
        if not url:
            new_jobs.append(job)
            continue
        h = _url_hash(url)
        if h not in seen:
            new_jobs.append(job)
            seen[h] = {
                "first_seen": datetime.now().strftime("%Y-%m-%d"),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "job_signature": _make_job_signature(
                    job.get("title", ""), job.get("company", "")
                ),
            }
    return new_jobs


def _make_job_signature(title: str, company: str) -> str:
    """Normalize title+company into a comparable signature.

    Example: "Senior Product Manager" + "FreshWorks Inc." → "senior product manager @ freshworks inc"
    """
    title = title.lower().translate(str.maketrans("", "", string.punctuation)).strip()
    company = company.lower().translate(str.maketrans("", "", string.punctuation)).strip()
    return f"{title} @ {company}"


def _dedup_fuzzy(jobs: list, seen: dict) -> list:
    """Remove fuzzy duplicates — same role posted on multiple boards.

    Uses SequenceMatcher with 0.85 threshold on job signatures.
    Only merges if first_seen dates are within ±3 days.
    """
    # Collect existing signatures from seen cache for cross-run dedup
    existing_sigs = []
    for entry in seen.values():
        sig = entry.get("job_signature")
        first_seen = entry.get("first_seen", "")
        if sig:
            existing_sigs.append((sig, first_seen))

    # Also collect signatures from the current batch
    batch_sigs = []
    kept = []

    for job in jobs:
        sig = _make_job_signature(job.get("title", ""), job.get("company", ""))
        first_seen = datetime.now().strftime("%Y-%m-%d")

        is_dup = False

        # Check against existing seen signatures
        for existing_sig, existing_date in existing_sigs:
            if _fuzzy_match(sig, existing_sig, existing_date, first_seen):
                is_dup = True
                break

        # Check against earlier items in this batch
        if not is_dup:
            for batch_sig, batch_date in batch_sigs:
                if _fuzzy_match(sig, batch_sig, batch_date, first_seen):
                    is_dup = True
                    break

        if not is_dup:
            kept.append(job)
            batch_sigs.append((sig, first_seen))
        else:
            logger.debug(f"Fuzzy dedup dropped: {sig}")

    dropped = len(jobs) - len(kept)
    if dropped:
        logger.info(f"Fuzzy dedup removed {dropped} duplicate(s)")
    return kept


def _fuzzy_match(sig_a: str, sig_b: str, date_a: str, date_b: str) -> bool:
    """True if signatures are similar (>=0.85) and dates within 3 days."""
    ratio = SequenceMatcher(None, sig_a, sig_b).ratio()
    if ratio < 0.85:
        return False
    # Check date proximity
    try:
        da = datetime.strptime(date_a, "%Y-%m-%d")
        db = datetime.strptime(date_b, "%Y-%m-%d")
        if abs((da - db).days) > 3:
            return False
    except (ValueError, TypeError):
        pass  # If dates can't be parsed, allow dedup
    return True


def _apply_filters(jobs: list, filters: dict) -> list:
    """Apply exclusion filters to job list."""
    exclude_titles = [t.lower() for t in filters.get("exclude_titles", [])]
    exclude_domains = [d.lower() for d in filters.get("exclude_domains", [])]
    max_days = filters.get("posted_within_days", 30)

    filtered = []
    for job in jobs:
        title = job.get("title", "").lower()
        # Exclude by title keywords
        if any(ex in title for ex in exclude_titles):
            continue
        # Exclude by domain keywords in description
        desc = job.get("description", "").lower()
        if any(ex in desc for ex in exclude_domains):
            continue
        # Exclude stale jobs
        days_ago = job.get("posted_days_ago")
        if days_ago is not None and days_ago > max_days:
            continue
        filtered.append(job)
    return filtered


def _estimate_days_ago(date_str: str) -> int:
    """Try to parse a date string into days ago. Returns None on failure."""
    if not date_str:
        return None
    date_str = date_str.lower().strip()

    # Handle relative dates
    if "today" in date_str or "just" in date_str:
        return 0
    if "yesterday" in date_str:
        return 1

    m = re.search(r"(\d+)\s*(day|hour|minute|min|hr)", date_str)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit in ("hour", "hr", "minute", "min"):
            return 0
        return num

    m = re.search(r"(\d+)\s*week", date_str)
    if m:
        return int(m.group(1)) * 7

    m = re.search(r"(\d+)\s*month", date_str)
    if m:
        return int(m.group(1)) * 30

    # Try ISO/standard date
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return (datetime.now() - dt).days
        except ValueError:
            continue

    return None


# ===================================================================
# Source: Indeed via JobSpy
# ===================================================================

def search_indeed(query: dict) -> list:
    """Search Indeed using python-jobspy library.

    Args:
        query: Dict with 'title', 'keywords', 'location' fields.

    Returns:
        List of job dicts with standardized fields.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.warning("python-jobspy not installed — skipping Indeed search")
        return []

    search_term = f"{query['title']} {query.get('keywords', '')}"
    location = query.get("location", "")

    try:
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=search_term,
            location=location if location.lower() != "remote" else "",
            results_wanted=20,
            hours_old=168,  # 7 days
            is_remote=location.lower() == "remote",
            country_indeed="USA",  # JobSpy requires country
        )

        jobs = []
        for _, row in df.iterrows():
            posted_days = None
            if hasattr(row, "date_posted") and row.date_posted:
                try:
                    dt = row.date_posted
                    if hasattr(dt, "date"):
                        posted_days = (datetime.now().date() - dt.date()).days
                    else:
                        posted_days = _estimate_days_ago(str(dt))
                except Exception:
                    pass

            jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company_name", row.get("company", ""))),
                "location": str(row.get("location", "")),
                "job_url": str(row.get("job_url", row.get("link", ""))),
                "description": str(row.get("description", "")),
                "posted_days_ago": posted_days,
                "source": "indeed",
                "date_posted": str(row.get("date_posted", "")),
            })
        logger.info(f"Indeed returned {len(jobs)} jobs for '{search_term}'")
        return jobs

    except Exception as e:
        logger.error(f"Indeed search failed: {e}")
        return []


# ===================================================================
# Source: Google Jobs scraping
# ===================================================================

def search_google_jobs(query: dict) -> list:
    """Search Google Jobs by scraping Google search results.

    Args:
        query: Dict with 'title', 'keywords', 'location' fields.

    Returns:
        List of job dicts.
    """
    search_term = f"{query['title']} {query.get('keywords', '')} jobs"
    location = query.get("location", "")
    if location and location.lower() != "remote":
        search_term += f" {location}"
    elif location.lower() == "remote":
        search_term += " remote"

    url = f"https://www.google.com/search?q={quote_plus(search_term)}&ibp=htl;jobs"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        jobs = []
        # Google Jobs uses specific data attributes
        for card in soup.select("div.BjJfJf, div[data-hveid], li.iFjolb"):
            title_el = card.select_one("div.BjJfJf, .PUpOsb, h2, .sH3zFd")
            company_el = card.select_one("div.vNEEBe, .nJlQNd, .company")
            location_el = card.select_one("div.Qk80Jf, .location")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""

            if title and "product" in title.lower():
                jobs.append({
                    "title": title,
                    "company": company,
                    "location": loc,
                    "job_url": "",
                    "description": "",
                    "posted_days_ago": None,
                    "source": "google_jobs",
                })

        logger.info(f"Google Jobs returned {len(jobs)} jobs for '{search_term}'")
        return jobs

    except Exception as e:
        logger.warning(f"Google Jobs search failed: {e}")
        return []


# ===================================================================
# Source: Company career pages
# ===================================================================

def search_career_pages(companies: dict) -> list:
    """Scrape career pages of watchlist companies for PM roles.

    Args:
        companies: Dict from company_watchlist.json["companies"].

    Returns:
        List of job dicts found on career pages.
    """
    jobs = []
    pm_patterns = re.compile(
        r"product\s*manager|product\s*lead|pm\s*[,\-–]|group\s*pm|principal\s*pm",
        re.IGNORECASE,
    )

    for company_name, info in companies.items():
        career_url = info.get("career_url", "")
        if not career_url:
            continue

        try:
            resp = requests.get(career_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Look for links/elements containing PM-related job titles
            for link in soup.find_all("a", href=True):
                text = link.get_text(strip=True)
                if pm_patterns.search(text):
                    href = link["href"]
                    if not href.startswith("http"):
                        # Resolve relative URL
                        from urllib.parse import urljoin
                        href = urljoin(career_url, href)

                    jobs.append({
                        "title": text,
                        "company": company_name,
                        "location": "",
                        "job_url": href,
                        "description": "",
                        "posted_days_ago": None,
                        "source": "career_page",
                    })

            # Also check plain text blocks
            for el in soup.find_all(["div", "span", "li", "h2", "h3", "h4"]):
                text = el.get_text(strip=True)
                if pm_patterns.search(text) and len(text) < 100:
                    # Try to find a parent link
                    parent_link = el.find_parent("a")
                    href = parent_link["href"] if parent_link and parent_link.get("href") else ""
                    if href and not href.startswith("http"):
                        from urllib.parse import urljoin
                        href = urljoin(career_url, href)

                    # Avoid duplicates within same company
                    if not any(j["title"] == text and j["company"] == company_name for j in jobs):
                        jobs.append({
                            "title": text,
                            "company": company_name,
                            "location": "",
                            "job_url": href,
                            "description": "",
                            "posted_days_ago": None,
                            "source": "career_page",
                        })

            logger.info(f"Career page {company_name}: found {sum(1 for j in jobs if j['company'] == company_name)} PM roles")
            time.sleep(1)  # Polite delay between companies

        except Exception as e:
            logger.warning(f"Career page scrape failed for {company_name}: {e}")

    return jobs


# ===================================================================
# Source: Naukri.com
# ===================================================================

def search_naukri(query: dict) -> list:
    """Search Naukri.com for PM roles (India-focused board).

    Args:
        query: Dict with 'title', 'keywords', 'location' fields.

    Returns:
        List of job dicts.
    """
    search_term = f"{query['title']} {query.get('keywords', '')}".strip()
    keyword_slug = search_term.lower().replace(" ", "-").replace(",", "")
    location = query.get("location", "").lower()

    # Naukri URL pattern
    url = f"https://www.naukri.com/{keyword_slug}-jobs"
    if location and location not in ("remote", ""):
        url += f"-in-{location.replace(' ', '-')}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        jobs = []
        # Naukri job cards
        for card in soup.select("article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple"):
            title_el = card.select_one("a.title, .info .title, .row1 a")
            company_el = card.select_one("a.subTitle, .comp-name, .row2 .comp-dtls-wrap a")
            loc_el = card.select_one("li.location span, .loc, .row2 .loc-wrap span")
            date_el = card.select_one("span.date, .job-post-day")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else ""
            date_text = date_el.get_text(strip=True) if date_el else ""

            href = ""
            if title_el and title_el.get("href"):
                href = title_el["href"]

            if title:
                jobs.append({
                    "title": title,
                    "company": company,
                    "location": loc,
                    "job_url": href,
                    "description": "",
                    "posted_days_ago": _estimate_days_ago(date_text),
                    "source": "naukri",
                    "date_posted": date_text,
                })

        logger.info(f"Naukri returned {len(jobs)} jobs for '{search_term}'")
        return jobs

    except Exception as e:
        logger.warning(f"Naukri search failed: {e}")
        return []


# ===================================================================
# Source: Bayt.com
# ===================================================================

def search_bayt(query: dict) -> list:
    """Search Bayt.com for PM roles (Middle East/MENA board).

    Args:
        query: Dict with 'title', 'keywords', 'location' fields.

    Returns:
        List of job dicts.
    """
    search_term = f"{query['title']} {query.get('keywords', '')}".strip()

    url = f"https://www.bayt.com/en/international/jobs/{quote_plus(search_term).lower().replace('+', '-')}-jobs/"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        jobs = []
        for card in soup.select("li[data-js-job], div.has-pointer-d"):
            title_el = card.select_one("h2 a, .jb-title a, a[data-js-aid='jobTitle']")
            company_el = card.select_one("b.jb-company, .jb-company, [data-js-aid='company']")
            loc_el = card.select_one("span.jb-loc, .jb-loc, [data-js-aid='location']")
            date_el = card.select_one("span.jb-date, .date-posted")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else ""
            date_text = date_el.get_text(strip=True) if date_el else ""

            href = ""
            if title_el and title_el.get("href"):
                href = title_el["href"]
                if href.startswith("/"):
                    href = "https://www.bayt.com" + href

            if title:
                jobs.append({
                    "title": title,
                    "company": company,
                    "location": loc,
                    "job_url": href,
                    "description": "",
                    "posted_days_ago": _estimate_days_ago(date_text),
                    "source": "bayt",
                    "date_posted": date_text,
                })

        logger.info(f"Bayt returned {len(jobs)} jobs for '{search_term}'")
        return jobs

    except Exception as e:
        logger.warning(f"Bayt search failed: {e}")
        return []


# ===================================================================
# Main orchestrator
# ===================================================================

def search_all_sources(criteria: dict, seen_jobs: dict = None,
                       progress_cb=None) -> list:
    """Run all queries across all sources, deduplicate, return raw job list.

    Args:
        criteria: Loaded from data/job_criteria.json.
        seen_jobs: Dedup cache dict. Loaded from seen_jobs.json if None.
        progress_cb: Optional callback(source, status, count).

    Returns:
        List of new (unseen) job dicts, filtered by criteria.
    """
    if seen_jobs is None:
        seen_jobs = load_seen_jobs()

    all_jobs = []
    search_matrix = criteria.get("search_matrix", [])
    watchlist = criteria.get("watchlist_companies", [])
    filters = criteria.get("filters", {})

    # Load company watchlist for career pages
    watchlist_path = os.path.join(DATA_DIR, "company_watchlist.json")
    companies = {}
    if os.path.exists(watchlist_path):
        with open(watchlist_path) as f:
            companies = json.load(f).get("companies", {})

    total_sources = len(search_matrix) * 3 + 1  # indeed+google+naukri per query + career pages
    completed = 0

    def _notify(source, status, count=0):
        nonlocal completed
        completed += 1
        if progress_cb:
            progress_cb(source, status, count)

    # Run each query across sources
    for query in search_matrix:
        # Indeed
        try:
            indeed_jobs = search_indeed(query)
            all_jobs.extend(indeed_jobs)
            _notify("Indeed", f"Found {len(indeed_jobs)} for '{query['title']}'", len(indeed_jobs))
        except Exception as e:
            logger.error(f"Indeed error: {e}")
            _notify("Indeed", f"Error: {e}")

        # Google Jobs
        try:
            google_jobs = search_google_jobs(query)
            all_jobs.extend(google_jobs)
            _notify("Google Jobs", f"Found {len(google_jobs)} for '{query['title']}'", len(google_jobs))
        except Exception as e:
            logger.error(f"Google Jobs error: {e}")
            _notify("Google Jobs", f"Error: {e}")

        # Naukri (only for India-relevant queries)
        location = query.get("location", "").lower()
        if location in ("india", "remote", ""):
            try:
                naukri_jobs = search_naukri(query)
                all_jobs.extend(naukri_jobs)
                _notify("Naukri", f"Found {len(naukri_jobs)}", len(naukri_jobs))
            except Exception as e:
                logger.error(f"Naukri error: {e}")
                _notify("Naukri", f"Error: {e}")

        # Bayt (only for Middle East/remote queries)
        if location in ("remote", "dubai", "uae", "middle east", ""):
            try:
                bayt_jobs = search_bayt(query)
                all_jobs.extend(bayt_jobs)
                _notify("Bayt", f"Found {len(bayt_jobs)}", len(bayt_jobs))
            except Exception as e:
                logger.error(f"Bayt error: {e}")
                _notify("Bayt", f"Error: {e}")

        time.sleep(0.5)  # Polite delay between queries

    # Career pages
    if companies:
        try:
            career_jobs = search_career_pages(companies)
            all_jobs.extend(career_jobs)
            _notify("Career Pages", f"Found {len(career_jobs)} from {len(companies)} companies", len(career_jobs))
        except Exception as e:
            logger.error(f"Career pages error: {e}")
            _notify("Career Pages", f"Error: {e}")

    # Dedup against seen jobs (exact URL match)
    new_jobs = _dedup_jobs(all_jobs, seen_jobs)

    # Fuzzy dedup (same title+company across boards)
    new_jobs = _dedup_fuzzy(new_jobs, seen_jobs)

    # Apply filters
    filtered_jobs = _apply_filters(new_jobs, filters)

    # Save updated seen cache
    save_seen_jobs(seen_jobs)

    logger.info(f"Search complete: {len(all_jobs)} total → {len(new_jobs)} new → {len(filtered_jobs)} after filters")
    return filtered_jobs


def search_urls(urls: list, progress_cb=None) -> list:
    """Score specific job URLs provided by user.

    Args:
        urls: List of job posting URLs.
        progress_cb: Optional callback(source, status, count).

    Returns:
        List of job dicts with fetched descriptions.
    """
    from researcher.jd_fetcher import fetch_full_jd

    jobs = []
    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue
        if progress_cb:
            progress_cb("URL", f"Fetching {i+1}/{len(urls)}: {url[:60]}...", i+1)

        job = {
            "title": "",
            "company": "",
            "location": "",
            "job_url": url,
            "description": "",
            "posted_days_ago": None,
            "source": "user_url",
        }

        # Fetch full JD text
        jd_text = fetch_full_jd(job)
        job["description"] = jd_text

        jobs.append(job)

    return jobs


# ===================================================================
# CLI testing
# ===================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with a simple search
    criteria = {
        "search_matrix": [
            {"zone": 1, "title": "Senior Product Manager", "keywords": "SaaS", "location": "remote"},
        ],
        "watchlist_companies": [],
        "filters": {
            "exclude_titles": ["Junior", "Associate", "Director", "VP"],
            "posted_within_days": 14,
        },
    }

    def progress(source, status, count=0):
        print(f"  [{source}] {status}")

    print("Searching...")
    jobs = search_all_sources(criteria, progress_cb=progress)
    print(f"\nFound {len(jobs)} jobs")
    for j in jobs[:5]:
        print(f"  - {j['title']} @ {j['company']} ({j['source']})")
