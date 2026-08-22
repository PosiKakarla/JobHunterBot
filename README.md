# Job Radar

Polls public ATS job feeds (Greenhouse, Lever) every ~10 minutes via GitHub
Actions and sends a Telegram alert when a new DevOps/SRE/Cloud/Platform role
suited to ~1-3 years experience appears. No server, no paid API.

## How it works

```
GitHub Actions (cron, every 10 min)
        ↓
check_jobs.py
   - fetches each company's public job feed (companies.json)
   - filters titles (include: devops/sre/cloud/platform/infra,
                      exclude: senior/staff/lead/architect/manager/etc.)
   - diffs against seen_jobs.json (committed back to repo each run)
        ↓
New relevant job? → Telegram message
```

## 1. Create your Telegram bot

1. Open Telegram, message **@BotFather** → send `/newbot` → follow the
   prompts → copy the **bot token** it gives you.
2. Send any message to your new bot (e.g. "hi") so it's allowed to message
   you back.
3. Visit this URL in your browser (replace `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id": ...}` in the response — that number is your
   **chat ID**.

## 2. Push this repo to GitHub

```bash
cd job-radar
git init
git add .
git commit -m "Initial job radar setup"
git branch -M main
git remote add origin https://github.com/<your-username>/job-radar.git
git push -u origin main
```

## 3. Add repo secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**

- `TELEGRAM_BOT_TOKEN` — the token from BotFather
- `TELEGRAM_CHAT_ID` — the chat ID from step 1.3

## 4. Enable the workflow

Go to the **Actions** tab in your repo — GitHub sometimes asks you to
confirm you want workflows enabled on a new repo. You can also trigger a
run manually right away via **Actions → Job Radar → Run workflow**
instead of waiting for the cron.

## Adding more companies

Edit `companies.json`. To find a company's ATS and slug:

1. Go to their careers page and click through to an actual job listing.
2. Look at the URL:
   - `boards.greenhouse.io/<slug>/jobs/...` or
     `job-boards.greenhouse.io/<slug>/jobs/...` → `"ats": "greenhouse"`
   - `jobs.lever.co/<slug>/...` → `"ats": "lever"`
3. Add an entry:
   ```json
   { "name": "Company Name", "ats": "greenhouse", "slug": "theslug" }
   ```

Start small (the current list has 8 companies), confirm you're getting
alerts correctly, then expand gradually — if one company's ATS changes
its response format, you want to catch that before you're monitoring 200
companies at once.

## Tuning the filters

Edit the keyword lists at the top of `check_jobs.py`:

- `INCLUDE_KEYWORDS` — a title must contain at least one of these
- `EXCLUDE_KEYWORDS` — a title containing any of these is dropped
- `MAX_YEARS_MENTIONED` — drops postings whose title mentions a years
  figure at or above this number

## Known limitations

- **Not instant.** GitHub Actions scheduled runs are best-effort — expect
  roughly 5-20 minute lag, not sub-minute alerts.
- **Greenhouse/Lever only for now.** Workday, SmartRecruiters, iCIMS etc.
  don't have a simple public JSON endpoint the same way — they'd need a
  separate, more fragile scraping approach.
- **Title-based filtering is approximate.** Some relevant roles get missed
  and some irrelevant ones slip through — tune the keyword lists as you go.
- **LinkedIn/Naukri are intentionally excluded** from automated polling —
  both restrict automated access in their terms, and their page structures
  change often. This tool leans on directly-published ATS feeds instead.
