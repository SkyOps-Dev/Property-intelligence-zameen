"""Offline tests: no request ever goes to zameen.com.

Database tests use a throwaway schema 'test_isb' inside the project database
(skipped if PostgreSQL isn't reachable). Run: pytest -q
"""
import copy
import gzip
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from scraper import categories, config, crawler, newads, pace, store
from scraper.crawler import CategoryCrawler
from scraper.fetcher import clean_old_raw
from scraper.newads import NewAdsCheck
from scraper.parser import ParseError, extract_state, parse_search_page

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "search_i11_rent.html")
CITY = {"name": "Islamabad", "slug": "/Islamabad-3"}


@pytest.fixture(scope="module")
def html():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def page(html):
    return parse_search_page(html)


# ---------- parser ----------

def test_page_identity(page):
    assert page.purpose == "for-rent"
    assert page.category_slug == "Houses_Property"
    assert page.location_slug == "/Islamabad_I_11-706"
    assert len(page.listings) == page.total_hits == 23


def test_listing_fields(page):
    l = next(x for x in page.listings if x["id"] == 54756960)
    assert l["price"] == 155000
    assert (l["purpose"], l["category_group"], l["type"], l["type_slug"]) == \
        ("for-rent", "Homes", "Houses", "Houses_Property")
    assert l["beds"] == 7 and l["baths"] == 7
    assert l["area_marla"] == 4.0
    assert l["location_path"].endswith("I-11 > I-11/2")
    assert l["title_ur"] and l["short_description"]
    assert l["contact_name"] == "Akhtar Nawaz Abbasi"
    assert l["mobile"] == "+923308883179" and l["whatsapp"] == "923308883179"
    assert l["agency_id"] == 192972 and l["listing_tier"] == "hot"
    assert "photo_count" not in l and "video_count" not in l
    assert l["zameen_created"].tzinfo is timezone.utc       # Zameen's times carry their zone
    assert l["url"].startswith("https://www.zameen.com/Property/")


def test_no_pictures_stored(page):
    for l in page.listings:
        assert not {"coverPhoto", "photoCount", "videoCount", "coverVideo"} & l["raw"].keys()
        assert "logo" not in (l["raw"].get("agency") or {})
    assert all("logo" not in a for a in page.agencies)


def test_blocked_page_raises():
    with pytest.raises(ParseError):
        parse_search_page("<html>captcha</html>")


# ---------- categories ----------

def test_category_urls():
    _, cats = categories.load()
    rent = next(c for c in cats if c.key == "for-rent/Houses_Property")
    sale = next(c for c in cats if c.key == "for-sale/Houses_Property")
    assert rent.url("/Islamabad-3") == "https://www.zameen.com/Rentals_Houses_Property/Islamabad-3-1.html"
    assert sale.url("/Islamabad_I_11-706", 2) == "https://www.zameen.com/Houses_Property/Islamabad_I_11-706-2.html"


def test_category_selection():
    _, both = categories.select(keys=["Houses_Property"])
    assert {c.key for c in both} == {"for-sale/Houses_Property", "for-rent/Houses_Property"}
    _, plots = categories.select(groups=["Plots"])
    assert len(plots) == 10 and all(c.group == "Plots" for c in plots)   # 2 plot types are for-sale only
    assert "for-rent/Residential_Plots" not in {c.key for c in plots}
    with pytest.raises(ValueError):
        categories.select(keys=["Castles"])


def test_cities(tmp_path):
    names = [c["name"] for c in categories.cities()]
    assert len(names) == len(set(names)) == 36 and names[0] == "Islamabad" and "Dera Ismail Khan" in names
    assert categories.city()["name"] == "Islamabad"                       # the default city
    assert categories.city("lahore")["slug"] == "/Lahore-1"                # any case
    assert all("enabled" not in c for c in categories.cities())          # no per-city switch: .env only
    with pytest.raises(ValueError):
        categories.city("Atlantis")
    # a city can skip categories that don't exist there
    cfg = json.loads(open(config.CATEGORIES_FILE, encoding="utf-8").read())
    cfg["cities"][1]["skip"] = ["for-rent/Plot_Files"]
    path = tmp_path / "categories.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    lahore, cats = categories.load(str(path), "Lahore")
    assert "for-rent/Plot_Files" not in {c.key for c in cats}
    assert "for-rent/Plot_Files" in {c.key for c in categories.load(str(path), "Islamabad")[1]}
    assert lahore["name"] == "Lahore" and cats[0].url(lahore["slug"]) == \
        "https://www.zameen.com/Houses_Property/Lahore-1-1.html"


def test_clean_old_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_KEEP_DAYS", 7)
    for d in ("2026-10-01", "2026-10-20", "2026-10-26", "notes"):
        (tmp_path / d).mkdir()
    assert clean_old_raw(today=date(2026, 10, 26)) == 1
    assert sorted(os.listdir(tmp_path)) == ["2026-10-20", "2026-10-26", "notes"]


# ---------- fake Zameen, built from the real saved page ----------

def make_page(html, purpose, cat_slug, loc_slug, ids, total_hits, total_pages, subs=None, posted=None,
              list_slug=None, types=None):
    """A search page like Zameen's, with our own listing IDs, totals, sub-areas {slug: ad count}
    and optionally the time every ad on it was posted (a datetime).
    A group list (e.g. Homes): list_slug='Homes', and each ad's own type in types {id: 'Flats_Apartments'}
    (ads not in types are cat_slug)."""
    state = copy.deepcopy(extract_state(html))
    template = state["algolia"]["content"]["hits"][0]
    hits = []
    for i in ids:
        h = copy.deepcopy(template)
        h["externalID"], h["purpose"] = str(i), purpose
        if posted:
            h["createdAt"] = posted.timestamp()
        h["category"][-1]["slug"] = (types or {}).get(i, cat_slug)
        hits.append(h)
    content = state["algolia"]["content"]
    content.update(hits=hits, nbHits=total_hits, nbPages=total_pages)
    f = state["algolia"]["filters"]
    f["purpose"]["value"] = purpose
    f["category.slug"]["value"] = {**f["category.slug"]["value"], "slug": list_slug or cat_slug}
    f["location.slug"]["value"] = [] if loc_slug == CITY["slug"] else [{"slug": loc_slug}]
    state["searchPageInternalLinks"]["data"]["locations"] = [
        {"location": {"slug": s, "name": s.strip("/")}, "ad_active_count": n} for s, n in (subs or {}).items()]
    return f"<script>window.state = {json.dumps(state)};window.webpackBundles=[]</script>"


class FakeZameen:
    """Answers search URLs from a dict {url: html}. Unknown URL -> 404 (None)."""

    def __init__(self, pages, interrupt_after=None):
        self.pages, self.calls, self.interrupt_after = pages, [], interrupt_after
        self.pages_fetched = 0
        self.failed = []          # pages the scraper reported as unusable (would be kept as FAILED copies)

    def get(self, url):
        if self.interrupt_after is not None and len(self.calls) >= self.interrupt_after:
            raise KeyboardInterrupt()
        self.calls.append(url)
        self.pages_fetched += 1
        return self.pages.get(url)

    def keep_failed(self, url, reason, html=None):
        self.failed.append(url)


def city_pages(html, cat, ids_a=range(1, 51), ids_b=range(100, 110)):
    """Islamabad too big (50 pages) -> split into areas A (2 pages) and B (1 page)."""
    p, s = cat.purpose, cat.slug
    A, B = "/Islamabad_Area_A-1", "/Islamabad_Area_B-2"
    ids_a = list(ids_a)
    return {
        cat.url(CITY["slug"], 1): make_page(html, p, s, CITY["slug"], ids_a[:25], 1250, 50, subs={A: 625, B: 625}),
        cat.url(A, 1): make_page(html, p, s, A, ids_a[:25], len(ids_a), 2),
        cat.url(A, 2): make_page(html, p, s, A, ids_a[25:], len(ids_a), 2),
        cat.url(B, 1): make_page(html, p, s, B, list(ids_b), len(ids_b), 1),
    }


# ---------- database ----------

@pytest.fixture
def conn():
    try:
        admin = store.connect()
    except Exception:
        pytest.skip("no database available (run: python main.py init-db)")
    admin.autocommit = True
    admin.cursor().execute("DROP SCHEMA IF EXISTS test_isb CASCADE; CREATE SCHEMA test_isb")
    sep = "&" if "?" in config.DATABASE_URL else "?"
    c = store.connect(config.DATABASE_URL + sep + "options=-csearch_path%3Dtest_isb")
    store.init_db(c)
    yield c
    c.close()
    admin.cursor().execute("DROP SCHEMA test_isb CASCADE")
    admin.close()


def _cat(key="for-rent/Houses_Property"):
    return next(c for c in categories.load()[1] if c.key == key)


def _one(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def test_full_pass_splits_and_completes(conn, html):
    cat = _cat()
    fake = FakeZameen(city_pages(html, cat))
    result = CategoryCrawler(conn, fake, CITY, cat).run()

    assert result["status"] == "done"
    assert len(fake.calls) == 4                      # city page 1, A1, A2, B1: city not paged further
    assert _one(conn, "SELECT count(*) FROM listings")[0] == 60
    assert _one(conn, "SELECT status FROM crawl_tasks WHERE location_slug = '/Islamabad-3'")[0] == "split"
    assert _one(conn, "SELECT count(*) FROM crawl_tasks WHERE status = 'done'")[0] == 2
    assert _one(conn, "SELECT expected_total FROM crawl_passes")[0] == 1250
    # 60 of 1250 is far below 90%: must NOT mark anything inactive
    assert "not marking anything inactive" in result["note"]


def test_area_with_ads_outside_its_sub_areas_is_read_whole(conn, html):
    """Like DHA Phase 2: 1,100 ads (44 pages), but its smaller areas hold only 100.
    Don't split: read all 44 pages of the area itself (Zameen has no 1,000-ad limit)."""
    cat = _cat()
    p, s, A = cat.purpose, cat.slug, "/Islamabad_Area_A-1"
    pages = {cat.url(CITY["slug"], 1): make_page(html, p, s, CITY["slug"], range(1, 26), 1100, 44, subs={A: 100})}
    for n in range(2, 45):
        pages[cat.url(CITY["slug"], n)] = make_page(html, p, s, CITY["slug"], range(n * 25 - 24, n * 25 + 1), 1100, 44)
    fake = FakeZameen(pages)
    result = CategoryCrawler(conn, fake, CITY, cat).run()

    assert result["status"] == "done" and result["errors"] == 0
    assert len(fake.calls) == 44 and cat.url(A, 1) not in fake.calls   # every page, past page 40; no split
    assert _one(conn, "SELECT count(*) FROM listings")[0] == 1100
    assert "saved 1100 of 1100" in result["note"]


def test_area_too_long_to_read_whole_is_split_anyway(conn, html, monkeypatch):
    """Like Lahore plots: 1,271 pages and 42 ads outside the smaller areas. Zameen fails past page ~696,
    so split anyway (missing those few ads) instead of reading the whole list."""
    monkeypatch.setattr(crawler, "READ_WHOLE_MAX_PAGES", 49)
    cat = _cat()
    pages = city_pages(html, cat)
    pages[cat.url(CITY["slug"], 1)] = make_page(html, cat.purpose, cat.slug, CITY["slug"], range(1, 26), 1290, 52,
                                                subs={"/Islamabad_Area_A-1": 625, "/Islamabad_Area_B-2": 625})
    fake = FakeZameen(pages)
    result = CategoryCrawler(conn, fake, CITY, cat).run()

    assert result["status"] == "done" and result["errors"] == 0
    assert len(fake.calls) == 4 and cat.url(CITY["slug"], 2) not in fake.calls   # split, city not paged


def test_resume_after_stop(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MIN_COVERAGE_FOR_INACTIVE", 0)
    cat = _cat()
    pages = city_pages(html, cat)

    with pytest.raises(KeyboardInterrupt):
        CategoryCrawler(conn, FakeZameen(pages, interrupt_after=2), CITY, cat).run()   # city p1 + A p1
    assert _one(conn, "SELECT status FROM crawl_passes")[0] == "paused"

    second = FakeZameen(pages)
    result = CategoryCrawler(conn, second, CITY, cat).run()
    assert result["status"] == "done"
    assert second.calls == [cat.url("/Islamabad_Area_A-1", 2), cat.url("/Islamabad_Area_B-2", 1)]  # no repeats
    assert _one(conn, "SELECT count(*) FROM crawl_passes")[0] == 1
    assert _one(conn, "SELECT count(*) FROM listings WHERE last_pass_id = 1")[0] == 60


def test_second_pass_tracks_changes_and_gone(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MIN_COVERAGE_FOR_INACTIVE", 0)
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()

    # next pass: area B lost listings 105-109, and listing 1 changed price
    pages = city_pages(html, cat, ids_b=range(100, 105))
    state_url = cat.url("/Islamabad_Area_A-1", 1)
    pages[state_url] = pages[state_url].replace('"price": 155000', '"price": 150000', 1)
    result = CategoryCrawler(conn, FakeZameen(pages), CITY, cat).run()

    assert result["status"] == "done" and result["inactive"] == 5
    assert _one(conn, "SELECT count(*) FROM listings WHERE NOT is_active")[0] == 5
    assert _one(conn, "SELECT count(*) FROM price_history WHERE listing_id = 1")[0] == 2
    assert _one(conn, "SELECT price FROM listings WHERE id = 1")[0] == 150000


def test_wrong_category_page_is_rejected(conn, html):
    cat = _cat()   # asks for houses FOR RENT, Zameen answers with houses FOR SALE
    pages = {cat.url(CITY["slug"], 1): make_page(html, "for-sale", "Houses_Property", CITY["slug"], [1], 1, 1)}
    fake = FakeZameen(pages)
    result = CategoryCrawler(conn, fake, CITY, cat).run()
    assert _one(conn, "SELECT count(*) FROM listings")[0] == 0           # nothing from the wrong category saved
    assert result["errors"] == 1
    assert "1 areas failed" in result["note"]
    assert fake.failed == [cat.url(CITY["slug"], 1)]                     # its copy is kept for debugging


def test_raw_copies_keep_only_failed_pages(tmp_path, monkeypatch):
    from scraper.fetcher import Fetcher
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_KEEP_DAYS", 2)
    monkeypatch.setattr(config, "RAW_COPIES", "failed")
    f = Fetcher()
    f._last_page = ("https://www.zameen.com/good-1.html", "<html>good page</html>")   # as if just downloaded
    f.keep_failed("https://www.zameen.com/other-1.html", "not the page just downloaded")  # nothing to keep
    f.keep_failed("https://www.zameen.com/good-1.html", "captcha page")
    files = [p for d in tmp_path.iterdir() for p in d.iterdir()]
    assert len(files) == 1 and files[0].name.startswith("FAILED-")
    text = gzip.open(files[0], "rt", encoding="utf-8").read()
    assert "FAILED: captcha page" in text and "good page" in text

    monkeypatch.setattr(config, "RAW_COPIES", "none")                     # no copies at all
    f.keep_failed("https://www.zameen.com/good-1.html", "again")
    assert len([p for d in tmp_path.iterdir() for p in d.iterdir()]) == 1


# ---------- quick check for new ads ----------

def newest_pages(html, cat, pages_of_ids, posted=(), total_hits=5000, total_pages=200, city=CITY, types=None):
    """Newest-first pages of the category's GROUP list (e.g. Homes for rent): {url: html} with the given
    listing IDs on page 1, 2, ..., posted at the given times (pages without one: posted just now).
    Ads are of cat's type unless types {id: type slug} says otherwise."""
    now = config.now()
    posted = list(posted) + [now] * len(pages_of_ids)
    group = categories.Group(cat.purpose, cat.group)
    return {f"{group.url(city['slug'], n)}?sort={config.NEWEST_SORT}":
            make_page(html, cat.purpose, cat.slug, city["slug"], ids, total_hits, total_pages, posted=when,
                      list_slug=cat.group, types=types)
            for n, (ids, when) in enumerate(zip(pages_of_ids, posted), start=1)}


def checked_yesterday(conn, cat, city=CITY):
    """As if this city's category had been checked for new ads a day ago (a check needs a starting time)."""
    with conn, conn.cursor() as cur:
        store.set_setting(cur, store.check_marker(city["name"], cat.key),
                          (config.now() - timedelta(days=1)).isoformat(timespec="seconds"))


@pytest.mark.parametrize("db_time_zone", ["Asia/Karachi", "UTC"])   # our PC, and a typical server
def test_new_check_reads_back_to_the_last_check(conn, html, db_time_zone):
    with conn, conn.cursor() as cur:
        cur.execute("SET TIME ZONE %s", (db_time_zone,))
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()   # ads 1-50 and 100-109 known
    now = config.now()
    pages = newest_pages(html, cat,
                         [range(1, 26),                           # refreshed old ads on top: nothing new...
                          [*range(200, 205), *range(26, 46)],     # ...but new ones below them
                          range(205, 230),                        # posted 30 min before the last pass: within the margin
                          [*range(46, 51), *range(100, 110)],     # posted 3 h before: back to the last check, stop
                          range(300, 325)],                       # never read
                         posted=[now, now, now - timedelta(minutes=30), now - timedelta(hours=3), now])
    fake = FakeZameen(pages)
    results = NewAdsCheck(conn, fake, [(CITY, [cat])]).run()

    assert len(fake.calls) == 4
    assert results[0]["new"] == 30 and not results[0]["capped"]
    assert _one(conn, "SELECT count(*) FROM listings")[0] == 90
    assert _one(conn, "SELECT status, new_listings, pages_fetched FROM new_checks") == ("done", 30, 4)
    # known ads keep the full pass that saw them (needed for marking ads inactive later)
    assert _one(conn, "SELECT count(*) FROM listings WHERE last_pass_id = 1")[0] == 60
    assert _one(conn, "SELECT count(*) FROM listings WHERE first_check_id = 1")[0] == 30   # only the new ones

    # the next check reads back to this check, not to the full pass
    pages = newest_pages(html, cat, [range(400, 425), range(1, 26)], posted=[now, now - timedelta(hours=2)])
    fake = FakeZameen(pages)
    NewAdsCheck(conn, fake, [(CITY, [cat])]).run()
    assert len(fake.calls) == 2


def test_new_check_reads_a_whole_group_in_one_list(conn, html):
    """Houses and flats for rent come mixed in one "Homes for rent" list: one page instead of two,
    and every ad still lands in its own category."""
    houses, flats = _cat("for-rent/Houses_Property"), _cat("for-rent/Flats_Apartments")
    checked_yesterday(conn, houses)
    checked_yesterday(conn, flats)
    pages = newest_pages(html, houses, [range(1, 26)], total_pages=1,
                         types={i: "Flats_Apartments" for i in range(1, 11)})       # 10 flats, 15 houses
    fake = FakeZameen(pages)
    results = NewAdsCheck(conn, fake, [(CITY, [houses, flats])]).run()

    assert len(fake.calls) == 1 and "/Rentals_Homes/" in fake.calls[0]
    assert results[0]["group"] == "for-rent/Homes" and results[0]["new"] == 25
    assert results[0]["by_category"] == {"for-rent/Flats_Apartments": 10, "for-rent/Houses_Property": 15}
    assert _one(conn, "SELECT count(*) FROM listings WHERE category_key = 'for-rent/Flats_Apartments'")[0] == 10
    assert _one(conn, "SELECT count(*) FROM listings WHERE category_key = 'for-rent/Houses_Property'")[0] == 15

    # only houses chosen: flats in the same list are passed over, not saved
    pages = newest_pages(html, houses, [range(100, 125)], total_pages=1, types={i: "Flats_Apartments" for i in range(100, 105)})
    results = NewAdsCheck(conn, FakeZameen(pages), [(CITY, [houses])]).run()
    assert results[0]["new"] == 20 and results[0]["passed_over"] == 5


def test_times_are_the_same_moment_on_any_server(conn, html):
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()
    shown_in_pakistan = _one(conn, "SELECT started_at FROM crawl_passes")[0]
    with conn, conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")                       # a server whose clock runs in UTC
    shown_in_utc = _one(conn, "SELECT started_at FROM crawl_passes")[0]
    assert shown_in_pakistan == shown_in_utc                   # the same moment...
    assert shown_in_pakistan.utcoffset() == timedelta(hours=5) and shown_in_utc.utcoffset() == timedelta(0)
    # Zameen's posted times come with their zone too
    created = _one(conn, "SELECT zameen_created FROM listings LIMIT 1")[0]
    assert created.tzinfo is not None
    # "last checked" times saved before time zones were added (no zone) count as Pakistan time
    other = "for-sale/Flats_Apartments"                         # a category without a full pass
    with conn, conn.cursor() as cur:
        store.set_setting(cur, store.check_marker("Islamabad", other), "2026-10-27T12:00:00")
        assert store.last_checked(cur, "Islamabad", other) == datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc)


def test_new_check_reads_past_1000_ads(conn, html):
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 46)], total_pages=45)  # all new
    results = NewAdsCheck(conn, FakeZameen(pages), [(CITY, [cat])]).run()
    assert results[0]["new"] == 1125 and results[0]["pages"] == 45 and not results[0]["capped"]


def test_new_check_flags_the_safety_limit(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MAX_PAGES", 3)
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 6)])
    results = NewAdsCheck(conn, FakeZameen(pages), [(CITY, [cat])]).run()
    assert results[0]["pages"] == 3 and results[0]["capped"]
    assert "page limit" in _one(conn, "SELECT note FROM new_checks")[0]


def test_new_check_stops_at_zameens_400_page_limit_of_newest_first_lists(conn, html, monkeypatch):
    """Page 401 of a newest-first list gives HTTP 500 (Islamabad plots, 2026-11-02): stop at 400, not fail.
    Saved and marked done up to there; the city keeps its pace and stays due."""
    monkeypatch.setattr(newads, "SORTED_MAX_PAGES", 2)
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 4)])   # page 3: "would 500"
    fake = FakeZameen(pages)
    results = NewAdsCheck(conn, fake, [(CITY, [cat])]).run()
    assert len(fake.calls) == 2 and results[0]["capped"] and not results[0]["error"] and results[0]["new"] == 50
    with conn, conn.cursor() as cur:
        assert store.last_checked(cur, CITY["name"], cat.key) > config.now() - timedelta(minutes=5)
        assert pace.get(cur, CITY["name"]) is None


def test_a_full_pass_after_the_last_check_is_the_new_starting_point(conn, html):
    """Check on Monday, full pass on Wednesday: Thursday's check reads back to Wednesday, not Monday."""
    cat = _cat()
    with conn, conn.cursor() as cur:
        store.set_setting(cur, store.check_marker("Islamabad", cat.key),
                          (config.now() - timedelta(days=3)).isoformat(timespec="seconds"))
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()
    with conn, conn.cursor() as cur:
        assert store.last_checked(cur, "Islamabad", cat.key) > config.now() - timedelta(minutes=5)


def test_paused_schedule_stops_a_running_check(conn, html, monkeypatch):
    monkeypatch.setattr(config, "new_checks_enabled", lambda: True)
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 6)])

    class PauseAfterTwoPages(FakeZameen):
        def get(self, url):
            if len(self.calls) == 2:
                with conn, conn.cursor() as cur:
                    store.set_setting(cur, "auto_check", "off")   # like pressing Pause in the viewer
            return super().get(url)

    fake = PauseAfterTwoPages(pages)
    NewAdsCheck(conn, fake, [(CITY, [cat])], scheduled=True).run()
    assert len(fake.calls) == 3                       # the page already loading finishes, then it stops
    assert _one(conn, "SELECT status, note FROM new_checks") == ("stopped", "paused while running")

    fake = FakeZameen(pages)                          # a check started by hand ignores the pause switch
    NewAdsCheck(conn, fake, [(CITY, [cat])]).run()
    assert len(fake.calls) > 0


def test_stop_button_stops_any_check(conn, html):
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 6)])

    class StopAfterTwoPages(FakeZameen):
        def get(self, url):
            if len(self.calls) == 2:
                with conn, conn.cursor() as cur:
                    store.set_setting(cur, "stop_check", "1")     # like pressing Stop in the viewer
            return super().get(url)

    fake = StopAfterTwoPages(pages)
    NewAdsCheck(conn, fake, [(CITY, [cat])]).run()                     # started by hand / from the viewer
    assert len(fake.calls) == 3
    assert _one(conn, "SELECT status, note FROM new_checks") == ("stopped", "stopped from the viewer")
    assert _one(conn, "SELECT count(*) FROM listings")[0] == 75     # what it found before stopping is kept


def test_stop_button_cuts_through_retry_waits(conn, html, monkeypatch):
    """Zameen refusing every page (503): Stop ends the check within seconds, not after the 6-minute retries."""
    from scraper import fetcher as fetcher_mod
    cat = _cat()
    checked_yesterday(conn, cat)
    clock = {"t": 0.0}
    monkeypatch.setattr(fetcher_mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(fetcher_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    class Refused:
        status_code, text = 503, "<title>Security check</title>"

    f = fetcher_mod.Fetcher()
    monkeypatch.setattr(f.session, "get", lambda url, timeout: Refused())
    with conn, conn.cursor() as cur:
        store.set_setting(cur, "stop_check", "1")        # Stop pressed while the first page is being refused
    monkeypatch.setattr(config, "RAW_COPIES", "none")
    NewAdsCheck(conn, f, [(CITY, [cat])]).run()
    assert _one(conn, "SELECT status, note FROM new_checks")[0] == "stopped"
    assert clock["t"] < 30                                 # stopped during the first 60 s wait, not after 360 s


def test_env_switch_off_stops_a_running_check(conn, html, monkeypatch):
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 6)])
    switch = {"on": True}
    monkeypatch.setattr(config, "new_checks_enabled", lambda: switch["on"])

    class SwitchOffAfterOnePage(FakeZameen):
        def get(self, url):
            switch["on"] = False                      # like setting NEW_CHECKS_ENABLED=false in .env
            return super().get(url)

    fake = SwitchOffAfterOnePage(pages)
    NewAdsCheck(conn, fake, [(CITY, [cat])], scheduled=True).run()
    assert len(fake.calls) == 1
    assert _one(conn, "SELECT status, note FROM new_checks") == ("stopped", "switched off in .env while running")


# ---------- pace: how often each city is checked ----------

def test_pace_busy_city_stays_at_the_shortest_interval():
    assert pace.next_every(6, new=340, hours=6, step=6, longest=24) == 6          # Lahore: ~340 new ads in 6 h
    assert pace.next_every(24, new=200, hours=24, step=6, longest=24) == 6        # busy after a long wait: at once


def test_pace_quiet_city_slows_down_one_doubling_at_a_time_up_to_the_maximum():
    every = 6
    seen = []
    for _ in range(4):                                                         # nothing new, check after check
        every = pace.next_every(every, new=0, hours=every, step=6, longest=24)
        seen.append(every)
    assert seen == [12, 24, 24, 24]
    assert pace.next_every(24, new=0, hours=24, step=6, longest=72) == 48      # a higher maximum: 48, then 72
    assert pace.next_every(48, new=0, hours=48, step=6, longest=72) == 72


def test_pace_medium_city_settles_and_intervals_are_whole_schedule_steps():
    # ~40 new ads a day (Rawalpindi-like): 25 ads take 15 h -> rounded down to 12 h, and stays there
    every = 6
    for _ in range(5):
        every = pace.next_every(every, new=round(40 * every / 24), hours=every, step=6, longest=24)
    assert every == 12
    for new, hours in [(1, 7), (3, 13), (0, 1), (17, 9.5)]:
        assert pace.next_every(12, new, hours, step=6, longest=24) % 6 == 0
    assert pace.next_every(12, new=5, hours=0, step=6, longest=24) == 12          # no time passed: keep it


def test_pace_sudden_burst_in_a_quiet_city_brings_it_back_at_once():
    assert pace.next_every(24, new=100, hours=24, step=6, longest=24) == 6       # a dealer uploads 100 ads
    assert pace.next_every(6, new=0, hours=6, step=6, longest=24) == 12          # then quiet again: slows down


def test_pace_simulated_month_never_waits_longer_than_the_maximum():
    """Fake clock, 30 days, a city per activity level: the interval follows the activity and stays in 6-24 h."""
    for per_day, expected in [(1400, 6), (900, 6), (40, 12), (5, 24), (0, 24)]:
        every, history = 6, []
        for _ in range(120):
            every = pace.next_every(every, new=round(per_day * every / 24), hours=every, step=6, longest=24)
            history.append(every)
        assert all(6 <= h <= 24 for h in history)
        assert history[-1] == expected, (per_day, history[-10:])


def test_pace_due_cities_with_a_fake_clock(conn):
    noon = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)                     # 12:00 in Pakistan
    with conn, conn.cursor() as cur:
        assert pace.is_due(cur, "Islamabad", noon)                              # never checked: due at once
        pace.record(cur, "Islamabad", noon, new=300, hours=6, step=6, longest=24)
        pace.record(cur, "Gujrat", noon - timedelta(hours=12), new=0, hours=24, step=6, longest=24)
        assert pace.get(cur, "Gujrat")["every"] == 12                          # first quiet check: one step slower
        pace.record(cur, "Gujrat", noon, new=0, hours=12, step=6, longest=24)
        assert pace.get(cur, "Islamabad")["every"] == 6 and pace.get(cur, "Gujrat")["every"] == 24
        later = noon + timedelta(hours=6)                                       # the next wake-up
        assert pace.due_cities(cur, ["Islamabad", "Gujrat"], later)[0] == ["Islamabad"]
        assert pace.due_cities(cur, ["Islamabad", "Gujrat"], noon + timedelta(hours=24))[0] == ["Islamabad", "Gujrat"]
        # a check that ran late (city checked at 13:10, every 6 h) is still due at the 18:00 wake-up, not 00:00
        pace.record(cur, "Lahore", noon + timedelta(minutes=70), new=300, hours=6, step=6, longest=24)
        assert pace.is_due(cur, "Lahore", later, step=6)


def test_new_check_sets_each_citys_pace(conn, html):
    """Islamabad: 100 new ads in a day -> every 6 h. Lahore: its list has no page (404) -> no ads, not a
    failure -> slows down to 12 h."""
    cat = _cat()
    checked_yesterday(conn, cat)
    checked_yesterday(conn, cat, LAHORE)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 5)], total_pages=4)
    results = NewAdsCheck(conn, FakeZameen(pages), [(CITY, [cat]), (LAHORE, [cat])]).run()

    assert [(r["city"], r["new"], r["error"], r["no_page"]) for r in results] == \
        [("Islamabad", 100, None, False), ("Lahore", 0, None, True)]
    with conn, conn.cursor() as cur:
        isb, lhr = pace.get(cur, "Islamabad"), pace.get(cur, "Lahore")
        assert isb["every"] == 6 and isb["new"] == 100 and 23.9 < isb["hours"] < 24.1
        assert lhr["every"] == 12 and lhr["new"] == 0
        assert lhr["next"] - lhr["last"] == timedelta(hours=12)
        assert store.last_checked(cur, "Lahore", cat.key) > config.now() - timedelta(minutes=5)   # marker moved on
    assert _one(conn, "SELECT errors FROM new_checks")[0] == 0


def test_failed_city_keeps_its_pace_and_stays_due(conn, html):
    cat = _cat()
    checked_yesterday(conn, cat)
    group = categories.Group(cat.purpose, cat.group)
    blocked = {f"{group.url(CITY['slug'], 1)}?sort={config.NEWEST_SORT}": "<html>Access denied</html>"}
    with conn, conn.cursor() as cur:
        pace.record(cur, "Islamabad", config.now() - timedelta(hours=30), new=0, hours=24)   # was every 12 h
    before = _one(conn, "SELECT value FROM settings WHERE key = 'pace:Islamabad'")[0]
    results = NewAdsCheck(conn, FakeZameen(blocked), [(CITY, [cat])]).run()

    assert results[0]["error"]
    assert _one(conn, "SELECT value FROM settings WHERE key = 'pace:Islamabad'")[0] == before   # unchanged
    with conn, conn.cursor() as cur:
        assert pace.is_due(cur, "Islamabad")                                   # tried again at the next run


def test_stopped_check_doesnt_set_the_pace(conn, html):
    cat = _cat()
    checked_yesterday(conn, cat)
    pages = newest_pages(html, cat, [range(n * 25, n * 25 + 25) for n in range(1, 6)])

    class StopAfterOnePage(FakeZameen):
        def get(self, url):
            with conn, conn.cursor() as cur:
                store.set_setting(cur, "stop_check", "1")
            return super().get(url)

    NewAdsCheck(conn, StopAfterOnePage(pages), [(CITY, [cat])]).run()
    with conn, conn.cursor() as cur:
        assert pace.get(cur, "Islamabad") is None and pace.is_due(cur, "Islamabad")


def test_scheduled_run_checks_only_the_cities_that_are_due(conn, monkeypatch):
    import argparse
    import main
    monkeypatch.setattr(main.store, "connect", lambda *a: conn)              # the test schema, not the real DB
    monkeypatch.setattr(config, "new_checks_enabled", lambda: True)
    monkeypatch.setattr(main.schedule, "is_paused", lambda c: False)
    monkeypatch.setattr(main, "clean_old_raw", lambda: 0)
    monkeypatch.setattr(main, "_log_to_file", lambda: None)
    checked = []

    class NoNetworkCheck:
        def __init__(self, conn, fetcher, jobs, scheduled):
            checked.extend(city["name"] for city, _ in jobs)
            assert all(len(cats) == 36 for _, cats in jobs)                    # every category of each city

        def run(self):
            return []

    monkeypatch.setattr(main, "NewAdsCheck", NoNetworkCheck)
    names = [c["name"] for c in categories.cities()]
    with conn, conn.cursor() as cur:                                           # all checked just now, quiet...
        for n in names:
            pace.record(cur, n, config.now(), new=0, hours=6)
        pace.record(cur, "Lahore", config.now() - timedelta(hours=6), new=400, hours=6)   # ...but Lahore: busy, due
    args = argparse.Namespace(scheduled=True, background=False, city=None, all=True, category=None, group=None)
    main.cmd_new(args)
    assert checked == ["Lahore"]

    checked.clear()
    with conn, conn.cursor() as cur:
        pace.record(cur, "Lahore", config.now(), new=400, hours=6)
    main.cmd_new(args)                                                         # nothing due: nothing loaded
    assert checked == []

    main.cmd_new(argparse.Namespace(**{**vars(args), "scheduled": False}))    # by hand: every city
    assert checked == names


def test_never_scraped_city_doesnt_get_a_pace(conn, html):
    cat = _cat()
    NewAdsCheck(conn, FakeZameen({}), [(LAHORE, [cat])]).run()                  # no starting time: skipped
    with conn, conn.cursor() as cur:
        assert pace.get(cur, "Lahore") is None


# ---------- several cities ----------

LAHORE = {"name": "Lahore", "slug": "/Lahore-1", "skip": []}


def one_page_city(html, cat, city, ids):
    """A city whose whole category fits on page 1."""
    return {cat.url(city["slug"], 1): make_page(html, cat.purpose, cat.slug, city["slug"], list(ids), len(ids), 1)}


def test_one_city_never_touches_another_citys_ads(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MIN_COVERAGE_FOR_INACTIVE", 0)
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()                  # Islamabad: 60 ads
    CategoryCrawler(conn, FakeZameen(one_page_city(html, cat, LAHORE, range(1001, 1026))), LAHORE, cat).run()
    assert _one(conn, "SELECT count(*) FROM listings WHERE city = 'Lahore'")[0] == 25
    assert _one(conn, "SELECT count(*) FROM crawl_passes WHERE city = 'Lahore'")[0] == 1     # its own pass

    # Lahore again, 15 of its ads gone: only those 15 become inactive, Islamabad's 60 stay active
    result = CategoryCrawler(conn, FakeZameen(one_page_city(html, cat, LAHORE, range(1001, 1011))), LAHORE, cat).run()
    assert result["inactive"] == 15 and result["city"] == "Lahore"
    assert _one(conn, "SELECT count(*) FROM listings WHERE city = 'Islamabad' AND is_active")[0] == 60


def test_new_check_skips_a_city_that_was_never_scraped(conn, html):
    cat = _cat()
    checked_yesterday(conn, cat)                                     # Islamabad has a starting time, Lahore none
    pages = {**newest_pages(html, cat, [range(500, 525)], total_pages=1),
             **newest_pages(html, cat, [range(600, 625)], total_pages=1, city=LAHORE)}
    fake = FakeZameen(pages)
    results = NewAdsCheck(conn, fake, [(CITY, [cat]), (LAHORE, [cat])]).run()

    assert [(r["city"], r["new"], r["skipped"]) for r in results] == [("Islamabad", 25, False), ("Lahore", 0, True)]
    assert all("Lahore" not in url for url in fake.calls)            # not a single Lahore page loaded
    assert "never fully scraped in Lahore" in _one(conn, "SELECT note FROM new_checks")[0]
    assert _one(conn, "SELECT cities FROM new_checks")[0] == "Islamabad, Lahore"


def test_sub_area_without_a_page_is_empty_not_failed(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MIN_COVERAGE_FOR_INACTIVE", 0)
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()     # ads 1-50 and 100-109
    pages = city_pages(html, cat)
    del pages[cat.url("/Islamabad_Area_B-2", 1)]                                 # area B: no page any more (404)
    result = CategoryCrawler(conn, FakeZameen(pages), CITY, cat).run()

    assert result["errors"] == 0 and "failed" not in result["note"]
    assert _one(conn, "SELECT status FROM crawl_tasks WHERE pass_id = 2 AND location_slug = '/Islamabad_Area_B-2'")[0] == "done"
    assert result["inactive"] == 10                    # B's ads are gone: the pass may mark them inactive


def test_category_without_a_page_is_empty_not_failed(conn, html):
    """Like Flats in Gujrat: Zameen has no page for the category in this city (404). Done with 0 ads,
    not failed, and still read every pass so its first ad is found later."""
    cat = _cat()
    result = CategoryCrawler(conn, FakeZameen({}), CITY, cat).run()

    assert result["status"] == "done" and result["errors"] == 0
    assert result["expected_total"] == 0 and "no page on Zameen" in result["note"]
    assert "failed" not in result["note"]
    with conn, conn.cursor() as cur:
        assert store.last_checked(cur, CITY["name"], cat.key) is not None   # counts as scraped for new-ad checks


def test_category_page_gone_never_marks_its_ads_inactive(conn, html, monkeypatch):
    monkeypatch.setattr(config, "MIN_COVERAGE_FOR_INACTIVE", 0)
    cat = _cat()
    CategoryCrawler(conn, FakeZameen(city_pages(html, cat)), CITY, cat).run()     # 60 ads
    result = CategoryCrawler(conn, FakeZameen({}), CITY, cat).run()             # then the whole page 404s

    assert result["inactive"] == 0
    assert _one(conn, "SELECT count(*) FROM listings WHERE is_active")[0] == 60


def test_pauses_when_blocked(conn, html):
    cat = _cat()
    subs = {f"/Islamabad_X{i}-{i}": 1250 for i in range(8)}  # 8 areas that all answer with a block page
    pages = {cat.url(CITY["slug"], 1): make_page(html, cat.purpose, cat.slug, CITY["slug"], [1], 9999, 400, subs)}
    pages.update({cat.url(s, 1): "<html>Access denied</html>" for s in subs})
    result = CategoryCrawler(conn, FakeZameen(pages), CITY, cat).run()
    assert result["status"] == "paused"
    assert "failures in a row" in result["note"]
