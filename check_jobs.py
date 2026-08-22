"""
Job Radar — polls public ATS job feeds (Greenhouse, Lever), filters for
DevOps/SRE/Cloud/Platform roles suited to ~1-3 years experience, and sends
Telegram alerts for new postings it hasn't seen before.

Run this on a schedule (see .github/workflows/job-check.yml). State
(which job IDs have already been alerted) is persisted in seen_jobs.json.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent
COMPANIES_FILE = ROOT / "companies.json"
SEEN_FILE = ROOT / "seen_jobs.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 15  # seconds

# --- Filtering rules -------------------------------------------------------

# Full-phrase matches — titles containing any of these pass immediately.
INCLUDE_KEYWORDS = [
    "devops", "dev ops",
    "sre", "site reliability",
    "cloud engineer", "cloud support", "cloud operations", "cloud infrastructure",
    "platform engineer",
    "infrastructure engineer", "infrastructure support",
    "system engineer", "systems engineer",
    "system administrator", "systems administrator",
    "linux administrator", "linux engineer",
    "production support",
    "build engineer", "release engineer",
]

# Fallback: a title also passes if it mentions a relevant technology AND a
# relevant role word — this catches titles like "AWS Support Engineer" or
# "Kubernetes Engineer" without needing every exact phrase spelled out above.
TECH_KEYWORDS = [
    "aws", "linux", "kubernetes", "terraform", "jenkins", "docker",
    "azure", "gcp", "ansible", "ci/cd", "cicd",
]
ROLE_KEYWORDS = ["engineer", "administrator", "support", "operations", "specialist"]

# Titles containing any of these are dropped, regardless of include matches.
EXCLUDE_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "architect",
    "manager", "director", "head of", "vp ", "vice president", "intern",
]

# Rough "years required" seniority filter — drop if title/desc mentions a
# minimum years figure at or above this threshold.
MAX_YEARS_MENTIONED = 4
YEARS_PATTERN = re.compile(r"(\d+)\s*\+?\s*years?", re.IGNORECASE)

# A job's location must contain at least one of these to pass. This is what
# stops global companies (Envoy Global, Tide, Capco, etc.) from flooding
# alerts with US/EU/LATAM roles. "remote" is included since remote-India or
# remote-APAC roles are often just labeled "Remote" with no country.
LOCATION_KEYWORDS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "chennai", "delhi", "gurgaon", "gurugram", "noida", "kolkata",
    "remote",
]


def is_relevant(title: str) -> bool:
    t = title.lower()
    if any(k in t for k in EXCLUDE_KEYWORDS):
        return False
    phrase_match = any(k in t for k in INCLUDE_KEYWORDS)
    tech_role_match = (
        any(tk in t for tk in TECH_KEYWORDS) and any(rk in t for rk in ROLE_KEYWORDS)
    )
    if not (phrase_match or tech_role_match):
        return False
    for match in YEARS_PATTERN.findall(t):
        if int(match) >= MAX_YEARS_MENTIONED:
            return False
    return True


def is_right_location(location: str) -> bool:
    loc = (location or "").lower()
    return any(k in loc for k in LOCATION_KEYWORDS)


# --- ATS fetchers ------------------------------------------------------------

def fetch_greenhouse(company: dict):
    slug = company["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    return [
        {
            "id": str(job["id"]),
            "title": job.get("title", ""),
            "location": (job.get("location") or {}).get("name", ""),
            "url": job.get("absolute_url", ""),
        }
        for job in jobs
    ]


def fetch_lever(company: dict):
    slug = company["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    postings = resp.json()
    return [
        {
            "id": str(p["id"]),
            "title": p.get("text", ""),
            "location": (p.get("categories") or {}).get("location", ""),
            "url": p.get("hostedUrl", ""),
        }
        for p in postings
    ]


# Search terms used to query Workday, since its public API is search-based
# rather than "list everything" like Greenhouse/Lever. Kept short to limit
# the number of requests per company per run.
WORKDAY_SEARCH_TERMS = [
    "devops", "cloud engineer", "site reliability",
    "linux administrator", "system administrator",
]


def fetch_workday(company: dict):
    host = company["host"]          # e.g. "accenture.wd103.myworkdayjobs.com"
    site = company["site"]          # e.g. "AccentureCareers"
    tenant = host.split(".")[0]     # e.g. "accenture"
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    jobs = []
    seen_paths = set()
    for term in WORKDAY_SEARCH_TERMS:
        resp = requests.post(
            url,
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        postings = resp.json().get("jobPostings", [])
        for p in postings:
            path = p.get("externalPath", "")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            jobs.append({
                "id": path,
                "title": p.get("title", ""),
                "location": p.get("locationsText", ""),
                "url": f"https://{host}{path}",
            })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
}


# --- Telegram ----------------------------------------------------------------

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping alert send.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=REQUEST_TIMEOUT,
    )
    if not resp.ok:
        print(f"Telegram send failed: {resp.status_code} {resp.text}", file=sys.stderr)


def format_alert(company_name: str, job: dict) -> str:
    return (
        f"🔥 <b>NEW MATCH</b>\n"
        f"Company: {company_name}\n"
        f"Role: {job['title']}\n"
        f"Location: {job['location'] or 'Not specified'}\n"
        f"Apply: {job['url']}"
    )


# --- Main --------------------------------------------------------------------

def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main():
    companies = load_json(COMPANIES_FILE, [])
    seen = load_json(SEEN_FILE, {})

    new_alerts = 0

    for company in companies:
        name = company["name"]
        ats = company["ats"]
        # State-tracking key: Greenhouse/Lever use "slug", Workday uses "host".
        state_key = company.get("slug") or company.get("host")

        fetcher = FETCHERS.get(ats)
        if not fetcher:
            print(f"SKIP {name}: unknown ATS '{ats}'")
            continue

        try:
            jobs = fetcher(company)
        except requests.RequestException as e:
            print(f"ERROR fetching {name} ({ats}/{state_key}): {e}", file=sys.stderr)
            continue

        seen_ids = set(seen.get(state_key, []))
        current_ids = set()

        for job in jobs:
            current_ids.add(job["id"])
            if job["id"] in seen_ids:
                continue  # already alerted before
            if not is_relevant(job["title"]):
                continue  # title doesn't match our filters
            if not is_right_location(job["location"]):
                continue  # not India / remote

            send_telegram(format_alert(name, job))
            new_alerts += 1

        # Persist all IDs currently on the board (relevant or not) so we
        # never re-alert on a job we've already evaluated once.
        seen[state_key] = list(seen_ids | current_ids)

    save_json(SEEN_FILE, seen)
    print(f"Done. {new_alerts} new alert(s) sent.")


if __name__ == "__main__":
    main()
