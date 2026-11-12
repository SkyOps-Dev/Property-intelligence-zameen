# Zameen Scrapper

Collects property ads from [Zameen.com](https://www.zameen.com) for **36 Pakistani cities** into a PostgreSQL
database and keeps them up to date: new ads, price changes, edits, and ads that disappear. Every field is
stored **except pictures and videos**. A small local website (the viewer) shows the data.

> The database holds dealers' names and phone numbers: keep it and the viewer private.

---

## Setup (once per machine)

**Needs:** Python 3.10+ (3.13 used), PostgreSQL 14+ (18 used).

```powershell
cd D:\Projects\zameen-islamabad
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env                 # then put your PostgreSQL password into DATABASE_URL
.venv\Scripts\python main.py init-db   # creates the database and tables (safe to run again)
.venv\Scripts\python -m pytest -q      # 44 offline tests, never contact Zameen
```

On Linux/macOS use `python3 -m venv .venv`, `.venv/bin/python` and `cp` instead.
Moving an existing database to a server: see [docs/DATABASE_TRANSFER.md](docs/DATABASE_TRANSFER.md).

---

## Daily use

### 1. Start PostgreSQL and the viewer

PostgreSQL must be running (Windows: *Services* → `postgresql-x64-18` → Start). Then:

```powershell
.venv\Scripts\python main.py web       # open http://127.0.0.1:5000  (Ctrl+C to stop)
```

| Page | Shows |
|---|---|
| **Overview** | Cities, ad counts, coverage, auto-scraping Pause / Resume |
| **Listings** | Search every ad by category, area, price, size, beds |
| **New listings** | Newly found ads (today / 3 / 7 / 30 days) and the **Check for new ads now** button |
| Ad page | All details, price check against similar ads, similar ads, map, contact, price history |

### 2. Scrape new ads by hand (runs once, stops by itself)

**In the viewer:** *New listings* → **▶ Check for new ads now**. It checks every city back to its last
check and stops when done. **■ Stop** ends it within seconds; ads found so far stay saved.

Or in the terminal:

```powershell
.venv\Scripts\python main.py new --all                 # every city
.venv\Scripts\python main.py new --city Lahore --all   # one city
```

Takes minutes if the last check was hours ago, about an hour after a day, a few hours after several days.

### 3. Auto-scraping (every 6 hours)

It runs only when **both** switches are on:

| | On | Off |
|---|---|---|
| `.env` | `NEW_CHECKS_ENABLED=true` | `NEW_CHECKS_ENABLED=false` |
| Schedule | **Resume** on the Overview page (or `main.py schedule resume`) | **Pause** (or `main.py schedule pause`) |

- Windows: install the task once with `.venv\Scripts\python main.py schedule install`. It wakes at
  00:00 / 06:00 / 12:00 / 18:00; the PC must be on, plugged in and logged in.
- Linux server: use cron instead:
  `0 */6 * * * cd /path/to/project && .venv/bin/python main.py new --all --scheduled`
- Each run checks only the cities that are **due** (busy cities every 6 h, quiet ones up to 24 h) and stops by itself.
- After scraping has been off for a while, press the button once first (catch-up), then switch auto on.

### 4. Full re-read (also finds sold / removed ads)

New-ad checks only add and update. A full re-read of a category also marks ads that are gone from Zameen:

```powershell
.venv\Scripts\python main.py run --city Lahore --category for-sale/Houses_Property
.venv\Scripts\python main.py run --city Multan --all
```

Big categories take hours. Ctrl+C stops; the same command resumes. **Spread big re-reads over separate days:**
thousands of pages in one day can get the scraper blocked.

### 5. If Zameen blocks the scraper

If checks fail with HTTP 503 and a "Security check | Zameen" page, Zameen is refusing the scraper.
Pause scraping and try one check again in a day or two.

---

## Commands

| Command | What it does | Loads Zameen? |
|---|---|---|
| `main.py web` | The viewer, http://127.0.0.1:5000 (this computer only) | No |
| `main.py new --all` / `--city X` | Check for new ads (all cities / one city) | Yes |
| `main.py run --city X --category KEY` | Full re-read of one category (`--group Homes`, `--all` also work) | Yes |
| `main.py schedule install / pause / resume / status / remove` | The every-6-hours task (Windows) | No |
| `main.py cities` | Each city: ads, last full scrape, check interval, next check | No |
| `main.py status` / `report` | Progress of recent passes / coverage per category | No |
| `main.py categories [--city X]` | The 36 categories and their Zameen addresses | No |
| `main.py init-db` | Create or update the tables | No |
Run them as `.venv\Scripts\python main.py …` (Linux: `.venv/bin/python main.py …`).
Only one scrape runs at a time; a second one refuses to start.

---

## Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | – | `postgresql://user:password@localhost:5432/zameen_islamabad` |
| `NEW_CHECKS_ENABLED` | `false` | Auto-scraping on / off (the button and `new` command ignore it) |
| `NEW_CHECK_EVERY_HOURS` | `6` | How often the schedule wakes (re-run `schedule install` after changing) |
| `NEW_CHECK_MAX_HOURS` | `24` | Longest a quiet city waits between checks |
| `DELAY_MIN` / `DELAY_MAX` | `3` / `5` | Seconds between page loads. **Keep at 3 or more** |
| `RAW_COPIES` / `RAW_KEEP_DAYS` | `failed` / `2` | Keep copies of pages that couldn't be used (`data/raw/`), for 2 days |

---

## The data

| Table | One row per |
|---|---|
| `listings` | ad: title, type, price, size (marla / sq ft), beds, baths, location, coordinates, dealer and phones, verified, tier, posted / updated on Zameen, first / last seen, active or gone, full raw record (`raw`) |
| `price_history` | first price and every price change |
| `listing_changes` | every other edit (title, contact, size…) |
| `agencies` | dealer company |
| `locations` | area tree (city > sector > block) |
| `crawl_passes`, `crawl_tasks` | full re-reads and their progress (for resuming) |
| `new_checks`, `settings` | new-ad checks, last-checked times, per-city pace, switches |

Prices are in PKR; `price / area_marla` gives price per marla. Beds / baths are 0 for plots and commercial ads.

---

## Project layout

```
main.py              all commands
categories.json      the 36 cities and 36 categories (Zameen codes)
db/schema.sql        the tables
scraper/             fetcher (downloads), parser (reads pages), crawler (full re-reads),
                     newads (new-ad checks), pace (per-city intervals), schedule, store (database)
web/                 the viewer (Flask app + templates)
tests/               offline tests and one saved Zameen page
docs/                database transfer guide
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `connection refused` / `could not connect` | PostgreSQL isn't running: start it in Windows Services |
| `password authentication failed` | Fix the password in `DATABASE_URL` in `.env` |
| `No module named psycopg2` (or other) | You're using the system Python: run `.venv\Scripts\python main.py …` |
| Checks fail: *failures in a row, probably blocked* / HTTP 503 | Zameen is refusing: Pause, wait a day or two, then try one check |
| Button greyed out | Another scrape is running; wait for it or press Stop |

---

## Rules

- Polite scraping: 3–5 seconds between pages, one scrape at a time, no huge one-day runs.
- Keep the database and viewer private (dealer contact data). The viewer listens on this computer only.
- Never commit `.env` (it holds the database password).
