"""NEW ADS: a quick check for ads posted since the last check, city by city, group by group.

How it works:
  1. For each group of a city (Homes / Plots / Commercial, for sale / for rent: 6 lists per city), open
     the group's list for the whole city, sorted newest first (by Zameen's "posted" time). The list
     holds all its types mixed together (houses, flats, portions...), and every ad says its own type,
     so each ad is still saved under its own category (e.g. for-sale/Houses_Property).
     Why groups: a quiet category (Rooms, Factories...) cost one page per check just to find nothing;
     in its group's list it costs nothing. About 6 pages per city instead of 36 when little is new.
  2. Read from the top and save every ad (new ones are added, known ones updated as usual).
  3. Stop once a page reaches ads posted before the group was last checked (minus MARGIN):
     everything below was there last time.
     Not "stop when nothing is new": when a dealer refreshes an old ad, Zameen gives it a new posted
     time and puts it back on top, so the first pages can be all known ads with new ones below them.
     Bonus: refreshed ads get their price and details updated too.
  Times are kept per category in settings ('new_check:<city>:<category key>'); a group reads back to
  the earliest of its categories. Before a category's first check, the start of its last full pass is
  used. A group with a category that was never fully scraped in that city is skipped: without a
  starting time the check would read every page (that's a full scrape's job:
  python main.py run --city <city> --all).
  Only the chosen categories are saved: ads of other types in the same group list are passed over.
  A group with no list at all in a city (404, e.g. no commercial ads for rent in Larkana) has nothing new.
  4. After a city's groups, its result sets how soon the city is checked again (see pace.py).

It never marks ads inactive: only a full pass (`run`) can tell an ad is gone.
Zameen serves a newest-first list only up to page 400 (SORTED_MAX_PAGES): after days without a check a busy
list can need more; the check then stops there ("capped", flagged in its note) and the older part is left
for a full scrape.

A scheduled check stops after the page it's on when it's paused (viewer button or `schedule pause`)
or switched off in .env (NEW_CHECKS_ENABLED=false). Any check stops after its current page when the
viewer's Stop button asks for it (settings.stop_check = its id).
"""
import logging
from datetime import timedelta

from . import config, pace, store
from .categories import groups_of
from .crawler import MAX_FAILURES_IN_A_ROW, WrongPage
from .fetcher import BlockedError
from .parser import ParseError, parse_search_page

log = logging.getLogger(__name__)

MARGIN = timedelta(hours=1)   # read a bit further back than the last check, to be safe
# Zameen serves a newest-first list only up to page 400 (10,000 ads): page 401 of Islamabad plots gave
# HTTP 500 three times (2026-11-02). Normal checks need far fewer pages (Islamabad plots: ~20 pages in 6 h);
# only a catch-up after days off gets near it. Stop there instead of failing: the older part is left
# for the full scrape, and it's flagged in the check's note.
SORTED_MAX_PAGES = 400
NO_PAGE = object()            # _fetch's answer when a group has no list in the city (no ads there)


class Paused(Exception):
    """Scheduled checks were paused or switched off while this one was running."""


class Blocked(Exception):
    pass


class NewAdsCheck:
    def __init__(self, conn, fetcher, jobs, scheduled=False):
        """jobs: [(city, [categories]), ...], checked in that order (each city group by group)."""
        self.conn = conn
        self.fetcher = fetcher
        self.jobs = jobs
        self.scheduled = scheduled
        self.check_id = None
        self.failures_in_a_row = 0
        # Stop / pause also cut through a refused page's long retry waits, not only between pages
        fetcher.should_stop = self._stop_if_paused

    def run(self):
        categories = sum(len(cats) for _, cats in self.jobs)
        cities = [city["name"] for city, _ in self.jobs]
        with self.conn, self.conn.cursor() as cur:
            self.check_id = store.open_check(cur, categories, self.scheduled, cities)
        log.info("=== new-ad check #%d: %s, %d categories in %d group lists, newest first", self.check_id,
                 ", ".join(cities), categories, sum(len(groups_of(cats)) for _, cats in self.jobs))
        results, status, stopped = [], "done", None
        try:
            for city, cats in self.jobs:
                started, city_results = config.now(), []
                for group, group_cats in groups_of(cats).items():
                    city_results.append(self._group(city, group, group_cats))
                    results.append(city_results[-1])
                self._set_pace(city, started, city_results)
        except KeyboardInterrupt:
            status, stopped = "stopped", "stopped by you"
            raise
        except Paused as e:
            status, stopped = "stopped", str(e)
        except Blocked as e:
            status, stopped = "failed", str(e)
        finally:
            self._finish(results, status, stopped)
        return results

    def _finish(self, results, status, stopped):
        name = lambda r: f"{r['city']} {r['group']}"
        notes = [stopped] if stopped else []
        capped = [name(r) for r in results if r["capped"]]
        failed = [name(r) for r in results if r["error"]]
        never = [r for r in results if r["skipped"]]
        if capped:
            notes.append(f"stopped at the {min(config.MAX_PAGES, SORTED_MAX_PAGES)}-page limit (older ads there are "
                         f"left for the full scrape): {', '.join(capped)}")
        if failed:
            notes.append(f"failed: {', '.join(failed)}")
        if never:
            notes.append(f"{len(never)} groups skipped: never fully scraped in "
                         f"{', '.join(sorted({r['city'] for r in never}))}")
        with self.conn, self.conn.cursor() as cur:
            store.set_check(cur, self.check_id, status=status, finished_at=config.now(),
                            note="; ".join(notes) or None)
        log.info("=== new-ad check #%d %s: %d new ads, %d pages%s", self.check_id, status,
                 sum(r["new"] for r in results), sum(r["pages"] for r in results),
                 f" ({'; '.join(notes)})" if notes else "")

    def _set_pace(self, city, started, results):
        """A city checked cleanly sets its own next interval; a failed or capped one keeps it and stays due."""
        read = [r for r in results if not r["skipped"]]
        if not read or any(r["error"] or r["capped"] for r in read):
            return
        # groups read back over different spans (each to its own last check): add up their rates
        rate = sum(r["new"] / r["hours"] for r in read if r["hours"] > 0)
        new = sum(r["new"] for r in read)
        hours = new / rate if rate else max(r["hours"] for r in read)
        with self.conn, self.conn.cursor() as cur:
            pace.record(cur, city["name"], started, new, hours)

    def _stop_if_paused(self):
        with self.conn, self.conn.cursor() as cur:
            if store.get_setting(cur, "stop_check") == str(self.check_id):
                raise Paused("stopped from the viewer")
        if not self.scheduled:
            return
        if not config.new_checks_enabled():
            raise Paused("switched off in .env while running")
        with self.conn, self.conn.cursor() as cur:
            if store.get_setting(cur, "auto_check", "on") == "off":
                raise Paused("paused while running")

    # ---------- one group in one city ----------

    def _group(self, city, group, cats):
        wanted = {c.key for c in cats}
        result = {"city": city["name"], "group": group.key, "pages": 0, "new": 0, "changed": 0,
                  "by_category": {}, "passed_over": 0, "capped": False, "error": None, "skipped": False,
                  "no_page": False, "hours": 0}
        label = f"{group.label} in {city['name']}"
        started = config.now()
        with self.conn, self.conn.cursor() as cur:
            since = [store.last_checked(cur, city["name"], c.key) for c in cats]
        if None in since:
            result["skipped"] = True
            never = [c.key for c, s in zip(cats, since) if s is None]
            log.info("%-36s skipped: never fully scraped (%s). Run a full scrape first", label, ", ".join(never))
            return result
        stop_before = min(since) - MARGIN      # back to the earliest of its categories
        result["hours"] = (started - min(since)).total_seconds() / 3600
        for p in range(1, min(config.MAX_PAGES, SORTED_MAX_PAGES) + 1):
            self._stop_if_paused()
            page = self._fetch(city, group, p)
            if page is NO_PAGE:
                result["no_page"] = True
                break
            if page is None:
                result["error"] = True
                break
            new = changed = 0
            with self.conn, self.conn.cursor() as cur:
                store.upsert_locations(cur, page.locations)
                store.upsert_agencies(cur, page.agencies)
                for listing in page.listings:
                    key = f"{listing['purpose']}/{listing['type_slug']}"   # the ad's own category
                    if key not in wanted:
                        result["passed_over"] += 1
                        continue
                    r = store.save_listing(cur, listing, city["name"], key, check_id=self.check_id)
                    new += r == "new"
                    changed += r == "changed"
                    if r == "new":
                        result["by_category"][key] = result["by_category"].get(key, 0) + 1
                store.add_check_counts(cur, self.check_id, pages=1, new=new, changed=changed)
            result["pages"] += 1
            result["new"] += new
            result["changed"] += changed
            oldest = min((l["zameen_created"] for l in page.listings if l["zameen_created"]), default=None)
            reached_old = oldest and oldest < stop_before
            if reached_old or not page.listings or p >= page.total_pages:
                break
        else:
            result["capped"] = True   # safety limit reached and still not back to the last check
        if not result["error"]:
            with self.conn, self.conn.cursor() as cur:
                for c in cats:
                    store.set_setting(cur, store.check_marker(city["name"], c.key), started.isoformat(timespec="seconds"))
        log.info("%-36s %4d new, %3d price changes, %2d pages%s", label, result["new"], result["changed"],
                 result["pages"], " -> stopped at the safety limit" if result["capped"] else
                 " -> FAILED" if result["error"] else " (no list on Zameen: no ads)" if result["no_page"] else "")
        return result

    def _fetch(self, city, group, page_no):
        """Download and parse one page of a group's list, newest first. Returns None on failure,
        NO_PAGE when page 1 doesn't exist (the group has no ads in this city)."""
        url = f"{group.url(city['slug'], page_no)}?sort={config.NEWEST_SORT}"
        try:
            html = self.fetcher.get(url)
            if html is None and page_no == 1:
                self.failures_in_a_row = 0
                return NO_PAGE
            if html is None:
                raise WrongPage("404 not found")
            page = parse_search_page(html)
            if page.purpose != group.purpose or page.category_slug != group.name:
                raise WrongPage(f"asked for {group.key}, Zameen returned {page.purpose}/{page.category_slug}")
            if page.location_slug and page.location_slug != city["slug"]:
                raise WrongPage(f"asked for {city['slug']}, Zameen returned {page.location_slug}")
            self.failures_in_a_row = 0
            return page
        except (BlockedError, ParseError, WrongPage, OSError, ValueError) as e:
            self.failures_in_a_row += 1
            log.error("failed %s: %s", url, e)
            self.fetcher.keep_failed(url, str(e))     # a copy of what Zameen sent, for debugging
            with self.conn, self.conn.cursor() as cur:
                store.add_check_counts(cur, self.check_id, errors=1)
            if self.failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                raise Blocked(f"{self.failures_in_a_row} failures in a row, probably blocked")
            return None
