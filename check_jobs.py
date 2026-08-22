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

INCLUDE_KEYWORDS = [
    "devops", "sre", "site reliability", "cloud engineer", "platform engineer",
    "infrastructure engineer", "system engineer", "systems engineer",
    "build engineer", "release engineer",
]

# Titles containing any of these are dropped, regardless of include matches.
EXCLUDE_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "architect",
    "manager", "director", "head of", "vp ", "vice president", "intern",
]

# Rough "years required" seniority filter — drop if title/desc mentions a
# minimum years figure at or above this threshold.
MAX_YEARS_MENTIONED = 4
YEARS_PATTERN = re.compile(r"(\d+)\s*\+?\s*years?", re.IGNORECASE)


def is_relevant(title: str) -> bool:
    t = title.lower()
    if not any(k in t for k in INCLUDE_KEYWORDS):
        return False
    if any(k in t for k in EXCLUDE_KEYWORDS):
        return False
    for match in YEARS_PATTERN.findall(t):
        if int(match) >= MAX_YEARS_MENTIONED:
            return False
    return True


# --- ATS fetchers ------------------------------------------------------------

def fetch_greenhouse(slug: str):
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


def fetch_lever(slug: str):
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


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
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
        slug = company["slug"]

        fetcher = FETCHERS.get(ats)
        if not fetcher:
            print(f"SKIP {name}: unknown ATS '{ats}'")
            continue

        try:
            jobs = fetcher(slug)
        except requests.RequestException as e:
            print(f"ERROR fetching {name} ({ats}/{slug}): {e}", file=sys.stderr)
            continue

        seen_ids = set(seen.get(slug, []))
        current_ids = set()

        for job in jobs:
            current_ids.add(job["id"])
            if job["id"] in seen_ids:
                continue  # already alerted before
            if not is_relevant(job["title"]):
                continue  # doesn't match our filters

            send_telegram(format_alert(name, job))
            new_alerts += 1

        # Persist all IDs currently on the board (relevant or not) so we
        # never re-alert on a job we've already evaluated once.
        seen[slug] = list(seen_ids | current_ids)

    save_json(SEEN_FILE, seen)
    print(f"Done. {new_alerts} new alert(s) sent.")


if __name__ == "__main__":
    main()