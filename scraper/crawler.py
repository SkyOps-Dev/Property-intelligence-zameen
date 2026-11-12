"""CRAWLER: scrapes one category (e.g. houses for rent) across one whole city, page by page.

How it walks:
  1. Open page 1 of the category for the whole city.
  2. If the list is long (over SPLIT_ABOVE_PAGES pages), go into the city's areas instead
     (sector -> sub-sector -> block), each with its own page 1, 2, 3 ... and split again where an
     area is still long. Short lists finish quickly, so ads that move around while we read
     (new, refreshed, removed) slip past less often.
     Some ads sit on an area itself, not in any of its smaller areas (e.g. "DHA Phase 2" with no
     sector). If the smaller areas' counts fall short of the area's total, the area isn't split:
     all its pages are read instead, unless it's longer than READ_WHOLE_MAX_PAGES (Zameen stops
     answering deep pages): then it's split anyway.
  3. Otherwise read every page. (Zameen lets anyone page through a whole search: no 1,000 limit.)

Progress is saved after every page (crawl_tasks.last_page), so a stopped run
continues exactly where it left off the next time you start the same category.
"""
import logging

from . import config, store
from .fetcher import BlockedError
from .parser import ParseError, parse_search_page

log = logging.getLogger(__name__)

MAX_FAILURES_IN_A_ROW = 5   # probably blocked: pause instead of hammering the site
NOT_IN_SUB_AREAS_MAX = 25   # more ads than this outside every listed smaller area: read the whole area, don't split
# ...unless the area is too long to read whole: Zameen answers 500 past page ~696 (Lahore plots, 2026-10-31,
# page 697 failed on two runs; page 400 worked). Then split anyway and accept missing the few outside ads.
READ_WHOLE_MAX_PAGES = 400


class WrongPage(Exception):
    """Zameen returned a page for a different category/area than we asked for."""


class PausedTooManyFailures(Exception):
    pass


class CategoryCrawler:
    def __init__(self, conn, fetcher, city, category):
        self.conn = conn
        self.fetcher = fetcher
        self.city = city
        self.cat = category
        self.pass_id = None
        self.failures_in_a_row = 0
        self.no_page = False        # the category has no page at all in this city (404): 0 ads, not an error
        self.label = f"{category.label} in {city['name']}"

    # ---------- the whole category ----------

    def run(self):
        with self.conn, self.conn.cursor() as cur:
            self.pass_id, resumed = store.open_pass(cur, self.city["name"], self.cat.key)
            if resumed:
                cur.execute("UPDATE crawl_tasks SET status = 'pending' WHERE pass_id = %s AND status = 'failed'",
                            (self.pass_id,))
            store.add_task(cur, self.pass_id, self.city["slug"], self.city["name"])
        log.info("=== %s (%s): %s pass #%d", self.label, self.cat.key,
                 "resuming" if resumed else "starting", self.pass_id)

        try:
            while True:
                with self.conn, self.conn.cursor() as cur:
                    task = store.next_task(cur, self.pass_id)
                if task is None:
                    break
                self._crawl_task(task)
        except KeyboardInterrupt:
            self._pause("stopped by you")
            raise
        except PausedTooManyFailures as e:
            self._pause(str(e))
            return self._summary()
        return self._finish()

    def _pause(self, why):
        with self.conn, self.conn.cursor() as cur:
            store.set_pass(cur, self.pass_id, status="paused", note=why)
        log.warning("%s: paused (%s). Run it again to continue where it stopped.", self.label, why)

    def _finish(self):
        with self.conn, self.conn.cursor() as cur:
            s = store.pass_summary(cur, self.pass_id)
            expected = s["expected_total"] or 0
            coverage = s["saved"] / expected if expected else (1.0 if s["saved"] == 0 else 0)
            notes = [f"saved {s['saved']} of {expected} ({coverage:.0%})"]
            inactive = 0
            if s["failed"]:
                notes.append(f"{s['failed']} areas failed")
            if self.no_page:
                # a whole category vanishing could also be Zameen renaming it: never mark ads gone on a 404 alone
                notes.append("no page on Zameen for this city (no ads)")
            elif s["failed"] == 0 and coverage >= config.MIN_COVERAGE_FOR_INACTIVE:
                inactive = store.mark_inactive(cur, self.city["name"], self.cat.key, self.pass_id)
            else:
                notes.append("not marking anything inactive: pass incomplete")
            store.set_pass(cur, self.pass_id, status="done", finished_at=config.now(), listings_saved=s["saved"],
                           marked_inactive=inactive, note="; ".join(notes))
        log.info("=== %s done: %s; %d marked inactive", self.label, "; ".join(notes), inactive)
        return self._summary()

    def _summary(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT status, expected_total, pages_fetched, new_listings, price_changes, errors, "
                        "marked_inactive, note FROM crawl_passes WHERE id = %s", (self.pass_id,))
            keys = ("status", "expected_total", "pages", "new", "changed", "errors", "inactive", "note")
            return {"pass_id": self.pass_id, "city": self.city["name"], "category": self.cat.key,
                    **dict(zip(keys, cur.fetchone()))}

    # ---------- one area ----------

    def _crawl_task(self, task):
        slug, depth = task["slug"], task["depth"]
        indent = "  " * depth
        total_pages = task["total_pages"]
        start = task["last_page"] + 1

        if start == 1:
            page = self._fetch(task, 1)
            if page is None:
                return
            total_pages = page.total_pages
            long_list = total_pages > config.SPLIT_ABOVE_PAGES and bool(page.sub_locations)
            outside = page.total_hits - sum(s["count"] or 0 for s in page.sub_locations)
            too_deep = total_pages > READ_WHOLE_MAX_PAGES
            split = long_list and (outside <= NOT_IN_SUB_AREAS_MAX or too_deep)
            with self.conn, self.conn.cursor() as cur:
                self._save(cur, page)
                fields = {"total_hits": page.total_hits, "total_pages": total_pages, "last_page": 1}
                if split:
                    for sub in page.sub_locations:
                        store.add_task(cur, self.pass_id, sub["slug"], sub["name"], slug, depth + 1)
                    fields["status"] = "split"
                store.update_task(cur, self.pass_id, slug, **fields)
                if depth == 0:
                    store.set_pass(cur, self.pass_id, expected_total=page.total_hits)
            log.info("%s%s: %d ads, %d pages%s", indent, task["name"], page.total_hits, total_pages,
                     f" -> too long to read whole: splitting into {len(page.sub_locations)} areas "
                     f"({outside} ads in no smaller area will be missed)" if split and outside > NOT_IN_SUB_AREAS_MAX else
                     f" -> splitting into {len(page.sub_locations)} areas" if split else
                     f" -> reading all pages ({outside} ads are in no smaller area)" if long_list else "")
            if split:
                return
            start = 2

        for p in range(start, min(total_pages or 0, config.MAX_PAGES) + 1):
            page = self._fetch(task, p)
            if page is None:
                return
            if not page.listings:
                break
            with self.conn, self.conn.cursor() as cur:
                self._save(cur, page)
                store.update_task(cur, self.pass_id, slug, last_page=p)

        with self.conn, self.conn.cursor() as cur:
            store.update_task(cur, self.pass_id, slug, status="done")

    def _fetch(self, task, page_no):
        """Download and parse one page. On failure marks the area failed and returns None
        (also None, but done, for a smaller area that no longer has a page)."""
        url = self.cat.url(task["slug"], page_no)
        try:
            html = self.fetcher.get(url)
            if html is None and page_no == 1:
                # no page = no ads there now, not an error: a smaller area from Zameen's own links that is
                # empty (counting it as failed would block marking gone ads inactive for this category
                # forever), or a category the city doesn't have yet (e.g. no flats in Gujrat). Read again
                # every pass, so the first ad posted there later is still found.
                log.info("%s%s: no page on Zameen (no ads there now)", "  " * task["depth"], task["name"])
                with self.conn, self.conn.cursor() as cur:
                    store.update_task(cur, self.pass_id, task["slug"], status="done", total_hits=0, total_pages=0)
                    if task["depth"] == 0:
                        self.no_page = True
                        store.set_pass(cur, self.pass_id, expected_total=0)
                return None
            if html is None:
                raise WrongPage("404 not found")
            page = parse_search_page(html)
            self._check(page, task)
            self.failures_in_a_row = 0
            with self.conn, self.conn.cursor() as cur:
                store.add_pass_counts(cur, self.pass_id, pages=1)
            return page
        except (BlockedError, ParseError, WrongPage, OSError, ValueError) as e:
            self.failures_in_a_row += 1
            log.error("failed %s: %s", url, e)
            self.fetcher.keep_failed(url, str(e))     # a copy of what Zameen sent, for debugging
            with self.conn, self.conn.cursor() as cur:
                store.add_pass_counts(cur, self.pass_id, errors=1)
                store.update_task(cur, self.pass_id, task["slug"], status="failed")
            if self.failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                raise PausedTooManyFailures(f"{self.failures_in_a_row} failures in a row, probably blocked")
            return None

    def _check(self, page, task):
        if page.purpose != self.cat.purpose or page.category_slug != self.cat.slug:
            raise WrongPage(f"asked for {self.cat.key}, Zameen returned {page.purpose}/{page.category_slug}")
        if page.location_slug and page.location_slug != task["slug"]:
            raise WrongPage(f"asked for area {task['slug']}, Zameen returned {page.location_slug}")

    def _save(self, cur, page):
        store.upsert_locations(cur, page.locations)
        store.upsert_agencies(cur, page.agencies)
        new = changed = 0
        for listing in page.listings:
            result = store.save_listing(cur, listing, self.city["name"], self.cat.key, self.pass_id)
            new += result == "new"
            changed += result == "changed"
        store.add_pass_counts(cur, self.pass_id, new=new, changed=changed)


def probe(conn, fetcher, city, category):
    """Load only page 1 of a category for a whole city: is the address right, and how big is it?"""
    url = category.url(city["slug"], 1)
    ok, note, page = False, None, None
    try:
        html = fetcher.get(url)
        if html is None:
            note = "404 not found: wrong category code?"
        else:
            page = parse_search_page(html)
            if page.purpose != category.purpose or page.category_slug != category.slug:
                note = f"Zameen returned {page.purpose}/{page.category_slug} instead"
            else:
                ok = True
    except Exception as e:  # report every kind of failure, keep probing the rest
        note = f"{e.__class__.__name__}: {e}"
    if note:
        fetcher.keep_failed(url, note)     # a copy of what Zameen sent (if anything), for debugging
    with conn, conn.cursor() as cur:
        store.save_probe(cur, city["name"], category.key, url, ok, page.total_hits if page else None,
                         page.total_pages if page else None, len(page.sub_locations) if page else None, note)
    return {"category": category, "url": url, "ok": ok, "note": note,
            "total_hits": page.total_hits if ok else None, "total_pages": page.total_pages if ok else None}
