"""All settings in one place. Values come from the .env file."""
import os
from datetime import datetime, timedelta, timezone

from dotenv import dotenv_values, load_dotenv

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/zameen_islamabad")

# Politeness: wait this many seconds (random between min and max) between page loads
DELAY_MIN = float(os.getenv("DELAY_MIN", "3"))
DELAY_MAX = float(os.getenv("DELAY_MAX", "5"))

# Zameen lets you page through every result (tested 2026-10-27: pages 41, 100, 400 of a 437-page search).
# A full pass still splits a search longer than SPLIT_ABOVE_PAGES into its areas (sector -> block): short
# lists finish fast, so ads moving around while we read (new, refreshed, removed) slip past less often.
SPLIT_ABOVE_PAGES = int(os.getenv("SPLIT_ABOVE_PAGES", "40"))
# Safety stop only (50,000 ads in one list): no real search is that long; it just prevents endless loops.
MAX_PAGES = int(os.getenv("MAX_PAGES", "2000"))

# Copies of downloaded pages (gzipped, in data/raw/<date>/), for checking what Zameen returned.
#   RAW_COPIES = failed: only pages that couldn't be used (block / captcha page, wrong page, layout change),
#                        named FAILED-*.html.gz: the useful ones for debugging. Good pages are in the DB anyway.
#              = all:    every page (≈200 KB each: ≈3.5 GB a week with all cities)
#              = none:   no copies
#   RAW_KEEP_DAYS: copies older than this are deleted when a run / check starts. 0 = don't keep any.
RAW_DIR = os.path.join(ROOT, os.getenv("RAW_DIR", "data/raw"))
RAW_COPIES = os.getenv("RAW_COPIES", "failed").strip().lower()
RAW_KEEP_DAYS = int(os.getenv("RAW_KEEP_DAYS", "2"))

# A finished category pass marks listings it didn't see as inactive (rented / sold),
# but only if it saved at least this share of what Zameen said it has. Protects against
# marking everything inactive after a run that was blocked half way.
MIN_COVERAGE_FOR_INACTIVE = float(os.getenv("MIN_COVERAGE_FOR_INACTIVE", "0.9"))

CATEGORIES_FILE = os.path.join(ROOT, "categories.json")

# TIME: every moment is stored with its time zone (UTC inside PostgreSQL), so it's right on any server,
# whatever its own clock zone. People see Pakistan time: every database connection shows times in it,
# and calendar days ("first seen 28 Oct") are Pakistan days.
DISPLAY_TZ = "Asia/Karachi"                      # for PostgreSQL (SET TIME ZONE)
PAKISTAN = timezone(timedelta(hours=5), "PKT")   # for Python (Pakistan has no daylight saving)


def now():
    """This moment, with its time zone (UTC)."""
    return datetime.now(timezone.utc)


def today():
    """Today's date in Pakistan."""
    return datetime.now(PAKISTAN).date()

# Quick check for new ads: Zameen's "newest first" sort, added to search addresses as ?sort=...
# Confirmed 2026-10-27: results come strictly by posted time (createdAt), newest first.
NEWEST_SORT = os.getenv("NEWEST_SORT", "date_desc")
LOG_DIR = os.path.join(ROOT, "logs")


def new_checks_enabled():
    """NEW_CHECKS_ENABLED=true/false in .env: master switch for the every-6-hours new-ad check.
    Read fresh from .env each time, so editing it takes effect without restarting anything.
    Off unless it says true. Checks started by hand (python main.py new) don't look at it."""
    value = dotenv_values(os.path.join(ROOT, ".env")).get("NEW_CHECKS_ENABLED") \
        or os.getenv("NEW_CHECKS_ENABLED", "false")
    return value.strip().lower() in ("true", "1", "yes", "on")

HEADERS = {
    # Trying Firefox instead of Chrome (which was blocked).
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Accept-Language": "en-US,en;q=0.9",
}
