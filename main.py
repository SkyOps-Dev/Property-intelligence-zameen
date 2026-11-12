"""Zameen scraper: scrapes property listings city by city, category by category, into PostgreSQL.

Commands without --city work on the default city (Islamabad, see categories.json).

Setup (once):
    python main.py init-db                         # creates the database and tables

See what there is (no internet):
    python main.py cities                          # every city: on/off, ads saved, last scrape
    python main.py categories --city Lahore        # a city's categories and their search addresses

Check a city's addresses and sizes (loads page 1 of each category):
    python main.py probe --city Lahore

Scrape (loads pages from zameen.com):
    python main.py run --category for-rent/Houses_Property                 # one category (Islamabad)
    python main.py run --city Lahore --all                                 # a whole city, category by category
    python main.py run --city Karachi --category Houses_Property           # houses, for sale and for rent
    python main.py run --group Plots                                       # every plot type
    Ctrl+C stops; the same command later continues where it stopped.

Quick check for new ads (newest first, back to the last check; a few minutes per city):
    python main.py new --all                       # every category of every city
    python main.py new --city Lahore --all         # one city

Automatically (Windows Task Scheduler, wakes up every NEW_CHECK_EVERY_HOURS hours and checks the cities
that are due: each city's interval follows how many new ads it gets, up to NEW_CHECK_MAX_HOURS).
One on/off switch for all cities: NEW_CHECKS_ENABLED=true/false in .env.
    python main.py schedule install | remove | pause | resume | status

Check results:
    python main.py status [--city Lahore]          # progress of current / last passes
    python main.py report [--city Lahore]          # per category: Zameen's count vs what we saved

View the data (local website, this computer only):
    python main.py web                             # then open http://127.0.0.1:5000

Offline test:
    python main.py parse-file tests/fixtures/search_i11_rent.html
"""
import argparse
import gzip
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from scraper import categories, config, pace, schedule, store
from scraper.crawler import CategoryCrawler, probe
from scraper.fetcher import Fetcher, clean_old_raw
from scraper.newads import NewAdsCheck
from scraper.parser import parse_search_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("main")


def cmd_init_db(_):
    if store.ensure_database():
        log.info("database created")
    store.init_db(store.connect())
    log.info("tables ready")


def _city(args):
    """The city chosen with --city, or the default city. Unknown names stop the program."""
    try:
        return categories.city(getattr(args, "city", None))
    except ValueError as e:
        sys.exit(str(e))


def cmd_cities(_):
    conn = store.connect()
    with conn.cursor() as cur:
        cur.execute("SELECT city, count(*) FILTER (WHERE is_active), count(*) FROM listings GROUP BY 1")
        saved = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("SELECT city, max(finished_at) FROM crawl_passes WHERE status = 'done' GROUP BY 1")
        last = dict(cur.fetchall())
        paces = {c["name"]: pace.get(cur, c["name"]) for c in categories.cities()}
    default = categories.city()["name"]
    print(f"  {'CITY':<18}{'CODE':<24}{'ACTIVE ADS':>11}{'ALL ADS':>9}  {'LAST FULL SCRAPE':<18}"
          f"{'NEW-AD CHECK EVERY':<20}NEXT CHECK")
    for c in categories.cities():
        active, total = saved.get(c["name"], (0, 0))
        finished = last.get(c["name"])
        p = paces[c["name"]]
        print(f"  {c['name']:<18}{c['slug']:<24}{active:>11,}{total:>9,}  "
              f"{f'{finished:%Y-%m-%d %H:%M}' if finished else '-':<18}"
              f"{f'{p['every']} h' if p else '-':<20}"
              f"{f'{p['next'].astimezone(config.PAKISTAN):%Y-%m-%d %H:%M}' if p else 'next run'}"
              f"{'   (default)' if c['name'] == default else ''}")
    print(f"\nAutomatic new-ad checks (all cities): {'ON' if config.new_checks_enabled() else 'OFF'} "
          f"(NEW_CHECKS_ENABLED in .env)")


def cmd_categories(args):
    try:
        city, cats = categories.load(city_name=args.city)
    except ValueError as e:
        sys.exit(str(e))
    skipped = f" ({len(city['skip'])} skipped)" if city["skip"] else ""
    print(f"{len(cats)} categories for {city['name']}{skipped}:\n")
    print(f"  {'KEY':<38}{'NAME':<30}{'CHECKED':<9}{'ON':<4}ADDRESS")
    for c in cats:
        print(f"  {c.key:<38}{c.label:<30}{'yes' if c.confirmed else 'no':<9}{'yes' if c.enabled else 'no':<4}"
              f"{c.url(city['slug'])}")
    print("\nCHECKED = no: the address is a best guess. Verify with: python main.py probe")


def _selected(args, city_name=None):
    if not (args.all or args.category or args.group):
        sys.exit("choose what to scrape: --category KEY, --group NAME or --all  (see: python main.py categories)")
    try:
        return categories.select(keys=args.category, groups=args.group, city_name=city_name or args.city)
    except ValueError as e:
        sys.exit(str(e))


def _connect_alone():
    """Database connection, but only if no other scrape is running (one at a time)."""
    conn = store.connect()
    if not store.take_scrape_lock(conn):
        log.warning("another scrape (run / probe / new) is already running: not starting")
        sys.exit(1)
    return conn


def cmd_probe(args):
    try:
        city, cats = (categories.select(args.category, args.group, city_name=args.city)
                      if (args.category or args.group) else categories.load(city_name=args.city))
    except ValueError as e:
        sys.exit(str(e))
    conn, fetcher = _connect_alone(), Fetcher()
    log.info("probing %d categories in %s (1 page each, %g-%gs apart)", len(cats), city["name"],
             config.DELAY_MIN, config.DELAY_MAX)
    results = []
    try:
        for c in cats:
            r = probe(conn, fetcher, city, c)
            results.append(r)
            log.info("%-40s %s", c.label, f"{r['total_hits']:,} ads, {r['total_pages']} pages" if r["ok"]
                     else f"PROBLEM: {r['note']}")
    except KeyboardInterrupt:
        log.warning("stopped by you")
    ok = [r for r in results if r["ok"]]
    total = sum(r["total_hits"] for r in ok)
    print(f"\n{city['name']}: {len(ok)} of {len(results)} categories OK, {total:,} ads in total")
    for r in results:
        if not r["ok"]:
            print(f"  PROBLEM  {r['category'].key}: {r['note']}  ({r['url']})")
    if len(ok) < len(results):
        print(f"\nA category with no page in {city['name']} can be skipped: add its key to \"skip\" "
              f"for {city['name']} in categories.json.")


def cmd_run(args):
    city, cats = _selected(args)
    removed = clean_old_raw()
    if removed:
        log.info("deleted %d old raw-copy folders", removed)
    conn, fetcher = _connect_alone(), Fetcher()
    log.info("%s: %d categories: %s", city["name"], len(cats), ", ".join(c.key for c in cats))
    results = []
    try:
        for c in cats:
            results.append(CategoryCrawler(conn, fetcher, city, c).run())
    except KeyboardInterrupt:
        log.warning("stopped by you. Saved data is kept; run the same command to continue.")
    print(f"\n{city['name'].upper()}\n{'CATEGORY':<38}{'STATUS':<9}{'PAGES':>7}{'NEW':>8}{'PRICE CHG':>11}"
          f"{'ERRORS':>8}  NOTE")
    for r in results:
        print(f"{r['category']:<38}{r['status']:<9}{r['pages']:>7}{r['new']:>8}{r['changed']:>11}{r['errors']:>8}"
              f"  {r['note'] or ''}")
    print(f"\n{fetcher.pages_fetched} pages downloaded this run")


def _log_to_file():
    """Scheduled checks run without a window (pythonw): send everything to logs/new-checks.log.
    The log rotates at 2 MB, keeping 5 old files, so it never fills the disk."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(config.LOG_DIR, "new-checks.log"), maxBytes=2_000_000,
                                  backupCount=5, encoding="utf-8")
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    sys.stdout = sys.stderr = _LogWriter()     # print() output and crash tracebacks go to the log too


class _LogWriter:
    """Stands in for stdout/stderr under pythonw (which has none): every line goes to the log."""
    def write(self, text):
        for line in text.rstrip().splitlines():
            log.info(line)

    def flush(self):
        pass


def cmd_new(args):
    if args.scheduled or args.background:
        _log_to_file()
    if args.city:                          # one city by hand
        names = [_city(args)["name"]]
    else:                                  # every city
        names = [c["name"] for c in categories.cities()]
    if args.scheduled and not config.new_checks_enabled():
        log.info("scheduled new-ad check skipped: switched off in .env (NEW_CHECKS_ENABLED=false)")
        return
    if args.scheduled and schedule.is_paused(store.connect()):
        log.info("scheduled new-ad check skipped: paused (resume with: python main.py schedule resume)")
        return
    if args.scheduled:                     # only the cities that are due (each city's own pace: scraper/pace.py)
        with store.connect() as conn, conn.cursor() as cur:
            names, waiting = pace.due_cities(cur, names)
        if waiting:
            log.info("not due yet: %s", ", ".join(f"{n} ({t.astimezone(config.PAKISTAN):%d %b %H:%M})" for n, t in waiting))
        if not names:
            log.info("scheduled new-ad check: no city is due")
            return
    jobs = [_selected(args, name) for name in names]
    removed = clean_old_raw()
    if removed:
        log.info("deleted %d old raw-copy folders", removed)
    conn, fetcher = _connect_alone(), Fetcher()
    try:
        results = NewAdsCheck(conn, fetcher, jobs, scheduled=args.scheduled).run()
    except KeyboardInterrupt:
        log.warning("stopped by you. New ads found so far are saved.")
        return
    print(f"\n{'CITY':<13}{'LIST':<22}{'NEW':>6}{'PRICE CHG':>11}{'PAGES':>7}  NEW BY CATEGORY / NOTE")
    for r in results:
        note = ("never fully scraped: skipped" if r["skipped"] else "stopped at the safety limit" if r["capped"]
                else "FAILED" if r["error"] else
                ", ".join(f"{k.split('/')[1]} {n}" for k, n in r["by_category"].items()))
        print(f"{r['city']:<13}{r['group']:<22}{r['new']:>6}{r['changed']:>11}{r['pages']:>7}  {note}")
    print(f"\n{sum(r['new'] for r in results)} new ads, {fetcher.pages_fetched} pages downloaded")


def cmd_schedule(args):
    if args.action == "install":
        ok, msg = schedule.install()
        if ok:
            schedule.set_paused(store.connect(), False)
        print(msg if not ok else f"Done: new-ad check every {schedule.EVERY_HOURS} hours, starting at 00:00. "
                                 f"Log: logs/new-checks.log")
    elif args.action == "remove":
        ok, msg = schedule.remove()
        print("Removed: no more automatic checks." if ok else msg)
    elif args.action in ("pause", "resume"):
        schedule.set_paused(store.connect(), args.action == "pause")
        print("Paused: scheduled checks won't run (a running one stops after its current page)."
              if args.action == "pause" else "Resumed: scheduled checks will run again.")
    info = schedule.task_info()
    paused = schedule.is_paused(store.connect())
    state = "OFF in .env (NEW_CHECKS_ENABLED=false)" if not config.new_checks_enabled() else \
        "PAUSED" if paused else "ON"
    if not info["installed"]:
        print("Schedule: not installed.  Set it up with: python main.py schedule install")
    else:
        print(f"Schedule: every {schedule.EVERY_HOURS} h, {state}. "
              f"Next run: {info['next_run']}. Last run: {info['last_run']} (result {info['last_result']})")
    names = [c["name"] for c in categories.cities()]
    with store.connect() as conn, conn.cursor() as cur:
        due, waiting = pace.due_cities(cur, names)
    print(f"Cities: all {len(names)} in categories.json, each at its own pace ({pace.STEP_HOURS} to "
          f"{pace.MAX_HOURS} h, see: python main.py cities). Due at the next run: {len(due)}, waiting: {len(waiting)}")


def cmd_status(args):
    city = args.city and _city(args)["name"]
    conn = store.connect()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.city, p.category_key, p.status, p.started_at, p.finished_at, p.expected_total,
                   (SELECT count(*) FROM listings l WHERE l.last_pass_id = p.id),
                   p.pages_fetched, p.errors,
                   (SELECT count(*) FROM crawl_tasks t WHERE t.pass_id = p.id AND t.status = 'pending'),
                   (SELECT count(*) FROM crawl_tasks t WHERE t.pass_id = p.id),
                   p.note
            FROM crawl_passes p WHERE %(city)s IS NULL OR p.city = %(city)s ORDER BY p.id DESC LIMIT 30
        """, {"city": city})
        rows = cur.fetchall()
    if not rows:
        print("No passes yet.")
        return
    print(f"{'#':>4}  {'CITY':<12}{'CATEGORY':<36}{'STATUS':<9}{'STARTED':<17}{'SAVED':>8}{'/ZAMEEN':>9}{'PAGES':>7}"
          f"{'ERR':>5}{'AREAS LEFT':>12}  NOTE")
    for (pid, cty, key, status, started, finished, expected, saved, pages, errors, left, areas, note) in rows:
        print(f"{pid:>4}  {cty:<12}{key:<36}{status:<9}{started:%Y-%m-%d %H:%M}{saved:>8}{(expected or 0):>9}"
              f"{pages:>7}{errors:>5}{f'{left}/{areas}':>12}  {note or ''}")


def cmd_report(args):
    try:
        city, cats = categories.load(city_name=args.city)
    except ValueError as e:
        sys.exit(str(e))
    conn = store.connect()
    p = {"city": city["name"]}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (category_key) category_key, ok, total_hits, checked_at
            FROM category_probes WHERE city = %(city)s ORDER BY category_key, id DESC
        """, p)
        probes = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("""
            SELECT DISTINCT ON (category_key) category_key, expected_total, listings_saved, finished_at
            FROM crawl_passes WHERE city = %(city)s AND status = 'done' ORDER BY category_key, id DESC
        """, p)
        passes = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("SELECT category_key, count(*) FILTER (WHERE is_active), count(*) FROM listings "
                    "WHERE city = %(city)s GROUP BY 1", p)
        counts = {r[0]: r[1:] for r in cur.fetchall()}
    print(f"{city['name'].upper()}\n{'CATEGORY':<36}{'ZAMEEN (probe)':>15}{'LAST PASS':>11}{'COVERAGE':>10}"
          f"{'ACTIVE':>9}{'ALL':>8}  FINISHED")
    for c in cats:
        probe_ok, probe_hits, _ = probes.get(c.key, (None, None, None))
        expected, saved, finished = passes.get(c.key, (None, None, None))
        active, total = counts.get(c.key, (0, 0))
        coverage = f"{saved / expected:.0%}" if expected else "-"
        probe_txt = "PROBLEM" if probe_ok is False else (f"{probe_hits:,}" if probe_hits is not None else "-")
        saved_txt = str(saved) if saved is not None else "-"
        finished_txt = f"{finished:%Y-%m-%d %H:%M}" if finished else "-"
        print(f"{c.key:<36}{probe_txt:>15}{saved_txt:>11}{coverage:>10}{active:>9}{total:>8}  {finished_txt}")


def cmd_parse_file(args):
    opener = gzip.open if args.file.endswith(".gz") else open      # kept copies in data/raw are gzipped
    with opener(args.file, "rt", encoding="utf-8") as f:
        page = parse_search_page(f.read())
    log.info("%s/%s in %s: page %s of %d, %d ads in total; %d listings, %d agencies, %d sub-areas on this page",
             page.purpose, page.category_slug, page.location_slug, page.page, page.total_pages, page.total_hits,
             len(page.listings), len(page.agencies), len(page.sub_locations))
    for l in page.listings[:5]:
        log.info("  %s | PKR %s | %s | %s marla | %s | %s", l["id"], f"{l['price']:,}", l["type"],
                 l["area_marla"], l["location_path"], l["contact_name"])


def cmd_web(args):
    from web.app import run
    log.info("viewer running at http://127.0.0.1:%d  (Ctrl+C to stop)", args.port)
    run(args.port)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Zameen scraper (city by city, category by category)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_city(p, help="city name, e.g. Lahore (default: the default city in categories.json)"):
        p.add_argument("--city", help=help)

    def add_selection(p, with_all=True, city_help=None):
        add_city(p, *([city_help] if city_help else []))
        p.add_argument("--category", action="append",
                       help="key like for-rent/Houses_Property, or a type like Houses_Property (repeatable)")
        p.add_argument("--group", action="append", help="Homes, Plots or Commercial (repeatable)")
        if with_all:
            p.add_argument("--all", action="store_true", help="every enabled category")

    sub.add_parser("init-db", help="create the database and tables").set_defaults(fn=cmd_init_db)
    sub.add_parser("cities", help="list cities: on/off, ads saved, last scrape").set_defaults(fn=cmd_cities)
    p = sub.add_parser("categories", help="list a city's categories and their addresses")
    add_city(p)
    p.set_defaults(fn=cmd_categories)
    p = sub.add_parser("probe", help="load page 1 of each category: check addresses and sizes")
    add_selection(p, with_all=False)
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("run", help="scrape categories of a city (resumes unfinished ones)")
    add_selection(p)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("new", help="quick check for new ads (newest first)")
    add_selection(p, city_help="one city (default: every city)")
    p.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)  # used by the Windows task
    p.add_argument("--background", action="store_true", help=argparse.SUPPRESS)  # started from the viewer
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("schedule", help="automatic new-ad check every NEW_CHECK_EVERY_HOURS hours")
    p.add_argument("action", choices=["install", "remove", "pause", "resume", "status"])
    p.set_defaults(fn=cmd_schedule)
    p = sub.add_parser("status", help="progress of recent passes")
    add_city(p, help="only this city (default: all)")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("report", help="coverage per category")
    add_city(p)
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("parse-file", help="parse a saved page (offline test)")
    p.add_argument("file")
    p.set_defaults(fn=cmd_parse_file)
    p = sub.add_parser("web", help="local website to browse the data")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(fn=cmd_web)

    args = ap.parse_args()
    args.fn(args)
