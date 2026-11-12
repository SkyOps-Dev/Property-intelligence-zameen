"""FETCHER: downloads pages politely (random delay, retries, back-off) and keeps short-lived raw copies.

Which copies are kept depends on RAW_COPIES (see config): by default only pages that couldn't be used.
The fetcher can't tell a wrong or broken page by itself, so whoever reads the page calls
keep_failed(url, reason) when it turns out unusable; refusals (403 / 429 / 5xx) are kept here.
"""
import gzip
import hashlib
import logging
import os
import random
import shutil
import time
from datetime import date, timedelta

import requests

from . import config

log = logging.getLogger(__name__)


class BlockedError(Exception):
    """Zameen kept refusing (403 / 429) or failing (5xx) after every retry."""


RETRY_STATUS = {403, 429, 500, 502, 503, 504}   # refused or a temporary server problem: wait and retry
STOP_CHECK_EVERY = 3   # seconds: how often a long wait asks should_stop()


class Fetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(config.HEADERS)
        self._last_request = 0.0
        self._last_page = None     # (url, html) of the last page downloaded, for keep_failed()
        self.pages_fetched = 0
        # optional: called every few seconds during the long retry waits; it raises to stop the run
        # (the viewer's Stop button would otherwise wait up to 6 minutes for a refused page to give up)
        self.should_stop = None

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.should_stop:
                self.should_stop()
            time.sleep(min(STOP_CHECK_EVERY, max(end - time.time(), 0)))

    def _wait(self):
        delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
        elapsed = time.time() - self._last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(self, url, retries=3):
        """Returns the page HTML, or None if Zameen says the page doesn't exist (404)."""
        refused = None
        for attempt in range(1, retries + 1):
            self._wait()
            self._last_request = time.time()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                log.warning("network error (%s), attempt %d/%d: %s", e.__class__.__name__, attempt, retries, url)
                self._sleep(10 * attempt)
                continue

            if r.status_code == 200:
                self.pages_fetched += 1
                self._last_page = (url, r.text)
                if config.RAW_COPIES == "all":
                    self._save_raw(url, r.text)
                return r.text
            if r.status_code == 404:
                return None
            if r.status_code in RETRY_STATUS:
                refused = r
                wait = 60 * attempt
                log.warning("got %d, waiting %ds before retrying: %s", r.status_code, wait, url)
                self._sleep(wait)
                continue
            r.raise_for_status()
        if refused is not None:   # what the block / error page said: worth keeping
            self.keep_failed(url, f"refused with {refused.status_code} after {retries} attempts", refused.text)
        raise BlockedError(f"gave up after {retries} attempts: {url}")

    def keep_failed(self, url, reason, html=None):
        """Keep a copy of a page that couldn't be used, named FAILED-*, with the reason at the top.
        Without html: the page just downloaded from this url (nothing is kept if there's none)."""
        if config.RAW_COPIES == "none":
            return
        if html is None:
            if not self._last_page or self._last_page[0] != url:
                return
            html = self._last_page[1]
        self._save_raw(url, html, failed=reason)

    def _save_raw(self, url, html, failed=None):
        if config.RAW_KEEP_DAYS <= 0:
            return
        folder = os.path.join(config.RAW_DIR, config.today().isoformat())
        os.makedirs(folder, exist_ok=True)
        name = ("FAILED-" if failed else "") + hashlib.md5(url.encode()).hexdigest()[:16] + ".html.gz"
        with gzip.open(os.path.join(folder, name), "wt", encoding="utf-8") as f:
            f.write(f"<!-- {url} -->\n" + (f"<!-- FAILED: {failed} -->\n" if failed else "") + html)


def clean_old_raw(today=None):
    """Delete raw copies older than RAW_KEEP_DAYS (folders are named by date: data/raw/2026-10-26)."""
    if not os.path.isdir(config.RAW_DIR):
        return 0
    oldest_kept = (today or config.today()) - timedelta(days=max(config.RAW_KEEP_DAYS, 0))
    removed = 0
    for name in os.listdir(config.RAW_DIR):
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue  # not a date folder (e.g. .gitkeep)
        if day < oldest_kept:
            shutil.rmtree(os.path.join(config.RAW_DIR, name), ignore_errors=True)
            removed += 1
    return removed
