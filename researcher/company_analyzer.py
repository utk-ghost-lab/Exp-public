"""
Phase 2 Component 4: Company Intelligence

Three signal types:
1. Career Page Velocity — PM role count on company career pages (rolling 30 days)
2. Funding Recency — web search for recent funding rounds
3. LinkedIn Hiring Signal — Google search for LinkedIn hiring posts

High velocity + recent funding = company in scaling mode → prioritize.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
WATCHLIST_PATH = os.path.join(DATA_DIR, "company_watchlist.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
REQUEST_TIMEOUT = 15


def load_watchlist() -> dict:
    """Load company watchlist from disk."""
    if os.path.exists(WATCHLIST_PATH):
        with open(WATCHLIST_PATH) as f:
            return json.load(f)
    return {"companies": {}}


def save_watchlist(watchlist: dict):
    """Persist watchlist to disk."""
    os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(watchlist, f, indent=2)


# ===================================================================
# Signal 1: Career Page Velocity
# ===================================================================

def check_career_page_velocity(company: str, career_url: str) -> int:
    """Count PM-related roles on a company's career page.

    Args:
        company: Company name (for logging).
        career_url: URL of the company's careers page.

    Returns:
        Number of PM-related job postings found.
    """
    if not career_url:
        return 0

    pm_pattern = re.compile(
        r"product\s*manager|product\s*lead|group\s*pm|principal\s*pm|"
        r"pm\s*[,\-–]|product\s*owner|product\s*analyst",
        re.IGNORECASE,
    )

    try:
        resp = requests.get(career_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        count = 0
        seen_titles = set()

        for el in soup.find_all(["a", "h2", "h3", "h4", "span", "div", "li"]):
            text = el.get_text(strip=True)
            if len(text) < 200 and pm_pattern.search(text):
                # Normalize to avoid counting same role twice
                normalized = text.lower().strip()[:60]
                if normalized not in seen_titles:
                    seen_titles.add(normalized)
                    count += 1

        logger.info(f"Career velocity for {company}: {count} PM roles found")
        return count

    except Exception as e:
        logger.warning(f"Career page check failed for {company}: {e}")
        return 0


# ===================================================================
# Signal 2: Funding Recency
# ===================================================================

def check_recent_funding(company: str) -> dict:
    """Search Google for recent funding rounds of a company.

    Args:
        company: Company name.

    Returns:
        Dict with funding info: {funded, amount, stage, date, source_snippet}.
    """
    query = f"{company} funding 2025 2026 series round"
    url = f"https://www.google.com/search?q={quote_plus(query)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        text = soup.get_text(separator=" ", strip=True).lower()

        # Look for funding signals
        funding_pattern = re.compile(
            r"(?:raised|secured|closed|funding|series)\s+.*?"
            r"(?:\$[\d.]+[mb]|₹[\d.]+\s*(?:cr|crore)|[\d.]+\s*(?:million|billion))",
            re.IGNORECASE,
        )
        matches = funding_pattern.findall(text)

        # Look for stage
        stage_match = re.search(r"series\s+[a-h]", text, re.IGNORECASE)
        stage = stage_match.group(0).title() if stage_match else None

        # Look for amount
        amount_match = re.search(r"\$[\d.]+[mb]|[\d.]+\s*(?:million|billion)", text, re.IGNORECASE)
        amount = amount_match.group(0) if amount_match else None

        # Look for year
        year_match = re.search(r"202[456]", text)
        date = year_match.group(0) if year_match else None

        funded = bool(matches or amount)

        result = {
            "funded": funded,
            "amount": amount,
            "stage": stage,
            "date": date,
            "source_snippet": matches[0][:100] if matches else None,
        }

        logger.info(f"Funding check for {company}: {'Found' if funded else 'None found'}")
        return result

    except Exception as e:
        logger.warning(f"Funding check failed for {company}: {e}")
        return {"funded": False, "amount": None, "stage": None, "date": None, "source_snippet": None}


# ===================================================================
# Signal 3: LinkedIn Hiring Signal
# ===================================================================

def check_linkedin_hiring_signal(company: str) -> dict:
    """Search Google for LinkedIn posts about hiring from company leadership.

    Args:
        company: Company name.

    Returns:
        Dict with {found, snippets, search_url}.
    """
    query = f'site:linkedin.com "{company}" "hiring" "product manager" 2026'
    url = f"https://www.google.com/search?q={quote_plus(query)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        snippets = []
        for result in soup.select("div.g, div[data-sokoban-container]"):
            snippet_el = result.select_one("span.st, div.VwiC3b, div[data-sncf]")
            if snippet_el:
                text = snippet_el.get_text(strip=True)
                if "hiring" in text.lower() or "product" in text.lower():
                    snippets.append(text[:150])

        result = {
            "found": len(snippets) > 0,
            "snippets": snippets[:3],
            "search_url": url,
        }

        logger.info(f"LinkedIn signal for {company}: {'Found' if snippets else 'None'}")
        return result

    except Exception as e:
        logger.warning(f"LinkedIn signal check failed for {company}: {e}")
        return {"found": False, "snippets": [], "search_url": url}


# ===================================================================
# Salary signal estimation
# ===================================================================

SALARY_BENCHMARKS = {
    "india":  {"currency": "₹", "low": 4000000, "high": 8000000, "label": "₹40-80L/year"},
    "uae":    {"currency": "AED", "low": 25000, "high": 40000, "label": "AED 25-40K/month"},
    "europe": {"currency": "€", "low": 80000, "high": 120000, "label": "€80-120K/year"},
    "uk":     {"currency": "£", "low": 70000, "high": 110000, "label": "£70-110K/year"},
    "us":     {"currency": "$", "low": 150000, "high": 250000, "label": "$150-250K/year"},
    "remote": {"currency": "$", "low": 100000, "high": 200000, "label": "$100-200K/year"},
}

_REGION_KEYWORDS = {
    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad",
              "pune", "chennai", "gurgaon", "gurugram", "noida", "kolkata"],
    "uae": ["dubai", "abu dhabi", "uae", "united arab emirates", "sharjah"],
    "uk": ["london", "uk", "united kingdom", "manchester", "birmingham", "edinburgh",
           "bristol", "cambridge", "oxford", "england", "scotland", "wales"],
    "europe": ["germany", "berlin", "amsterdam", "paris", "france", "netherlands",
               "spain", "madrid", "barcelona", "ireland", "dublin", "sweden",
               "stockholm", "denmark", "copenhagen", "europe", "lisbon", "portugal"],
    "us": ["us", "usa", "united states", "new york", "san francisco", "seattle",
           "austin", "boston", "chicago", "los angeles", "california", "texas",
           "washington", "colorado", "denver"],
    "remote": ["remote", "anywhere", "distributed"],
}


def _detect_region(location: str) -> str:
    """Map a location string to a region key. Returns 'remote' as default."""
    loc_lower = location.lower()
    for region, keywords in _REGION_KEYWORDS.items():
        if region == "remote":
            continue  # Check remote last
        for kw in keywords:
            if kw in loc_lower:
                return region
    if any(kw in loc_lower for kw in _REGION_KEYWORDS["remote"]):
        return "remote"
    return "remote"  # Default fallback


def estimate_salary_signal(company: str, location: str) -> dict:
    """Estimate salary range for a Senior PM role at a company.

    Searches Google for salary data and compares against regional benchmarks.

    Returns:
        {region, benchmark, below_target, estimated_range, source_snippet}
    """
    region = _detect_region(location)
    benchmark = SALARY_BENCHMARKS.get(region, SALARY_BENCHMARKS["remote"])

    query = f'"{company}" "senior product manager" salary'
    url = f"https://www.google.com/search?q={quote_plus(query)}"

    result = {
        "region": region,
        "benchmark": benchmark["label"],
        "below_target": False,
        "estimated_range": None,
        "source_snippet": None,
    }

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        text = resp.text.lower()

        # Extract salary numbers based on currency patterns
        patterns = [
            (r"\$\s*([\d,]+)\s*k", lambda m: int(m.group(1).replace(",", "")) * 1000),
            (r"\$([\d,]+)", lambda m: int(m.group(1).replace(",", ""))),
            (r"₹\s*([\d.]+)\s*l", lambda m: int(float(m.group(1)) * 100000)),
            (r"₹\s*([\d,]+)", lambda m: int(m.group(1).replace(",", ""))),
            (r"aed\s*([\d,]+)", lambda m: int(m.group(1).replace(",", ""))),
            (r"£\s*([\d,]+)", lambda m: int(m.group(1).replace(",", ""))),
            (r"€\s*([\d,]+)", lambda m: int(m.group(1).replace(",", ""))),
        ]

        extracted = []
        for pat, converter in patterns:
            for m in re.finditer(pat, text):
                try:
                    val = converter(m)
                    if val > 0:
                        extracted.append(val)
                except (ValueError, IndexError):
                    pass

        if extracted:
            low_est = min(extracted)
            high_est = max(extracted)
            result["estimated_range"] = f"{low_est:,} - {high_est:,}"
            # Below target if high estimate < benchmark low
            result["below_target"] = high_est < benchmark["low"]

            # Grab a snippet for context
            soup = BeautifulSoup(resp.text, "html.parser")
            for el in soup.select("div.BNeawe, span.st, div.VwiC3b"):
                snippet = el.get_text(strip=True)
                if any(c in snippet.lower() for c in ("$", "₹", "£", "€", "aed", "salary")):
                    result["source_snippet"] = snippet[:120]
                    break

        logger.info(f"Salary signal for {company} ({region}): {result.get('estimated_range', 'no data')}")

    except Exception as e:
        logger.warning(f"Salary estimation failed for {company}: {e}")

    return result


# ===================================================================
# Hiring spike detection
# ===================================================================

def _detect_hiring_spike(history: list, current_count: int) -> dict:
    """Detect if a company has a sudden hiring spike.

    Spike = 3+ roles currently AND avg 0 roles in the prior 30-day window (days 7-37).

    Args:
        history: List of {"date": "YYYY-MM-DD", "count": int} entries.
        current_count: Current PM role count.

    Returns:
        {"spike": bool, "current": int, "avg_30d": float, "reason": str}
    """
    if current_count < 3:
        return {"spike": False, "current": current_count, "avg_30d": 0.0, "reason": ""}

    # Look at entries from 7-37 days ago (the prior 30-day window)
    today = datetime.now().date()
    window_start = today - timedelta(days=37)
    window_end = today - timedelta(days=7)

    prior_counts = []
    for entry in history:
        try:
            d = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        if window_start <= d <= window_end:
            prior_counts.append(entry["count"])

    avg_30d = sum(prior_counts) / len(prior_counts) if prior_counts else 0.0

    spike = current_count >= 3 and avg_30d == 0.0
    reason = ""
    if spike:
        reason = (
            f"Went from avg {avg_30d:.0f} PM roles to {current_count} — "
            f"company appears to be scaling PM team"
        )

    return {
        "spike": spike,
        "current": current_count,
        "avg_30d": avg_30d,
        "reason": reason,
    }


# ===================================================================
# Company-level orchestration
# ===================================================================

def analyze_company(company_name: str, config: dict) -> dict:
    """Run all 3 signals for a company.

    Args:
        company_name: Company name.
        config: Company config dict from watchlist (with career_url, etc.)

    Returns:
        Enriched company dict with updated signal data.
    """
    career_url = config.get("career_url", "")

    # Signal 1: Career page velocity
    pm_count = check_career_page_velocity(company_name, career_url)
    config["pm_roles_30d"] = pm_count

    # Maintain rolling history for spike detection
    history = config.get("pm_roles_history", [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    # No same-day duplicates
    if not history or history[-1].get("date") != today_str:
        history.append({"date": today_str, "count": pm_count})
    else:
        history[-1]["count"] = pm_count
    # Prune entries older than 60 days
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    history = [e for e in history if e.get("date", "") >= cutoff]
    config["pm_roles_history"] = history

    # Detect hiring spike
    config["hiring_spike"] = _detect_hiring_spike(history, pm_count)

    time.sleep(1)  # Polite delay

    # Signal 2: Funding recency
    funding = check_recent_funding(company_name)
    if funding.get("funded"):
        config["last_funding"] = {
            "amount": funding.get("amount"),
            "stage": funding.get("stage"),
            "date": funding.get("date"),
        }

    time.sleep(1)

    # Signal 3: LinkedIn hiring signal
    linkedin = check_linkedin_hiring_signal(company_name)
    config["linkedin_signal"] = linkedin if linkedin.get("found") else None

    # Update priority based on signals
    priority_score = 0
    if pm_count >= 3:
        priority_score += 2  # High velocity
    elif pm_count >= 1:
        priority_score += 1
    if funding.get("funded") and funding.get("date") in ("2025", "2026"):
        priority_score += 2  # Recent funding
    if linkedin.get("found"):
        priority_score += 1

    if priority_score >= 3:
        config["priority"] = "high"
    elif priority_score >= 1:
        config["priority"] = "medium"
    else:
        config["priority"] = "low"

    config["last_analyzed"] = datetime.now().strftime("%Y-%m-%d")
    return config


def update_watchlist(watchlist: dict = None, progress_cb=None) -> dict:
    """Run all signals for all companies in watchlist.

    Args:
        watchlist: Watchlist dict. Loaded from file if None.
        progress_cb: Optional callback(company, status).

    Returns:
        Updated watchlist dict (also saved to disk).
    """
    if watchlist is None:
        watchlist = load_watchlist()

    companies = watchlist.get("companies", {})
    total = len(companies)

    for i, (name, config) in enumerate(companies.items()):
        if progress_cb:
            progress_cb(name, f"Analyzing {i+1}/{total}")

        try:
            companies[name] = analyze_company(name, config)
        except Exception as e:
            logger.error(f"Failed to analyze {name}: {e}")
            if progress_cb:
                progress_cb(name, f"Error: {e}")

    watchlist["companies"] = companies
    watchlist["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_watchlist(watchlist)

    return watchlist


# ===================================================================
# CLI
# ===================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    def progress(company, status):
        print(f"  [{company}] {status}")

    print("Updating company watchlist...")
    wl = update_watchlist(progress_cb=progress)
    print(f"\nDone. Analyzed {len(wl.get('companies', {}))} companies.")
    for name, info in wl.get("companies", {}).items():
        print(f"  {name}: priority={info.get('priority')}, pm_roles={info.get('pm_roles_30d')}")
