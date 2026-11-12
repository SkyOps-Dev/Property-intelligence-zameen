"""VIEWER: a small local website to browse the scraped data. Read-only, except the switch that
pauses / resumes the scheduled new-ad checks and the new-ad check Start / Stop buttons.
Every page can be narrowed to one city with ?city=Lahore (the city switch in the header); no city = all.

Start it with:  python main.py web     then open http://127.0.0.1:5000
It only listens on this computer: the data includes dealers' phone numbers, keep it private.
"""
import json
import time
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

from flask import Flask, abort, g, redirect, render_template, request, url_for

from scraper import categories, config, pace, schedule, store

PER_PAGE = 50
GOOD_COVERAGE, OK_COVERAGE = 0.95, 0.80

# what the "sort" dropdown may ask for (never put user input straight into SQL)
SORTS = {
    "newest": ("Newest on Zameen", "l.zameen_created DESC NULLS LAST"),
    "price_asc": ("Price: low to high", "l.price ASC NULLS LAST"),
    "price_desc": ("Price: high to low", "l.price DESC NULLS LAST"),
    "size_desc": ("Size: largest first", "l.area_marla DESC NULLS LAST"),
    "per_marla": ("Price per marla: low to high", "l.price / NULLIF(l.area_marla, 0) ASC NULLS LAST"),
}

# "Posted on Zameen" filter: label and hours
POSTED = {"24h": ("Last 24 hours", 24), "3d": ("Last 3 days", 72), "7d": ("Last 7 days", 168)}

app = Flask(__name__)


def db():
    """One database connection per page view, closed when the page is done."""
    if "db" not in g:
        g.db = store.connect()
        g.db.autocommit = True
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.before_request
def same_site_posts_only():
    """Buttons (POST) only work when pressed on our own pages: another website open in the browser
    can't secretly start or stop a scrape (cross-site request forgery)."""
    if request.method == "POST":
        source = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if urlparse(source).netloc != request.host:
            abort(403)


def query(sql, params=None, one=False):
    with db().cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


_task_info = {"at": 0, "info": None}


def task_info():
    """Windows schedule status, asked at most once a minute (asking Windows takes ~0.2 s)."""
    if time.monotonic() - _task_info["at"] > 60:
        _task_info.update(at=time.monotonic(), info=schedule.task_info())
    return _task_info["info"]


def category_labels():
    """'for-sale/Houses_Property' -> 'Houses for sale', for every category in categories.json."""
    return {c.key: c.label for c in categories.load()[1]}


def selected_city():
    """The city chosen with ?city=Lahore (any case), or None for all cities."""
    name = (request.args.get("city") or "").lower()
    return next((c["name"] for c in categories.cities() if c["name"].lower() == name), None)


def city_filter(column="city"):
    """SQL condition and params for the chosen city (or all cities)."""
    city = selected_city()
    return (f"{column} = %(city)s", {"city": city}) if city else ("TRUE", {})


@app.context_processor
def helpers():
    city = selected_city()
    # the city switch keeps you on the same page (an ad's own page switches to Listings)
    endpoint = request.endpoint if request.endpoint in ("overview", "listings", "new_listings") else "listings"
    switch = [{"name": None, "label": "All cities", "url": url_for(endpoint)}] + \
             [{"name": c["name"], "label": c["name"], "url": url_for(endpoint, city=c["name"])}
              for c in categories.cities()]
    return {"labels": category_labels(), "city": city, "city_switch": switch}


@app.template_filter("pkr")
def pkr(value):
    """155000 -> '1.55 Lakh', 25000000 -> '2.5 Crore' (how prices are read in Pakistan)."""
    if value is None:
        return "-"
    if value >= 10_000_000:
        return f"{value / 10_000_000:.2f}".rstrip("0").rstrip(".") + " Crore"
    if value >= 100_000:
        return f"{value / 100_000:.2f}".rstrip("0").rstrip(".") + " Lakh"
    return f"{value:,}"


@app.template_filter("num")
def num(value):
    if not isinstance(value, (int, float, Decimal)):   # None or missing: show a dash, never crash the page
        return "-"
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}".rstrip("0").rstrip(".")


@app.template_filter("pct")
def pct(value):
    return "-" if value is None else f"{min(value, 1):.0%}"


@app.template_filter("level")
def level(coverage):
    """Colour for a coverage share: good / ok / low."""
    if coverage is None:
        return "none"
    return "good" if coverage >= GOOD_COVERAGE else "ok" if coverage >= OK_COVERAGE else "low"


@app.template_filter("pk_time")
def pk_time(moment):
    """A Python time (any zone) as Pakistan time: '02 Nov, 18:00'."""
    return moment.astimezone(config.PAKISTAN).strftime("%d %b, %H:%M") if moment else "-"


@app.template_filter("duration")
def duration(start, end=None):
    if not start:
        return "-"
    minutes = int(((end or config.now()) - start).total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


@app.template_filter("ago")
def ago(moment):
    """How long ago, in plain words: 'just now', '5 h ago', '3 days ago', '12 Oct 2026'."""
    if not moment:
        return "-"
    minutes = int((config.now() - moment).total_seconds() // 60)
    if minutes < 60:
        return "just now" if minutes < 2 else f"{minutes} min ago"
    if minutes < 24 * 60:
        return f"{minutes // 60} h ago"
    days = minutes // (24 * 60)
    if days < 31:
        return "yesterday" if days == 1 else f"{days} days ago"
    return moment.astimezone(config.PAKISTAN).strftime("%d %b %Y")


SMALL_WORDS = {"IN", "FOR", "AND", "OF", "THE", "ON", "AT", "TO", "WITH", "NEAR", "IS", "A", "AN", "BY", "OR"}


@app.template_filter("nice_title")
def nice_title(title):
    """Titles typed in ALL CAPS read as shouting: 'DHA PHASE 7 VALLEY PLOT FOR SALE' -> 'DHA Phase 7 Valley
    Plot for Sale'. Short codes (DHA, F-7, 5C) stay as they are; normal titles are left alone."""
    if not title:
        return "-"
    letters = [c for c in title if c.isalpha() and c.isascii()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return title
    words = []
    for i, w in enumerate(title.split(" ")):
        if w.upper() in SMALL_WORDS and i:
            words.append(w.lower())
        elif len(w) <= 3 or any(c.isdigit() for c in w):
            words.append(w)                      # DHA, F-7, 10A ...
        else:
            words.append(w.capitalize())
    return " ".join(words)


@app.template_filter("area")
def area(path):
    """Without province and city: 'Islamabad Capital > Islamabad > F-7 > F-7/2' -> 'F-7 > F-7/2',
    'Punjab > Lahore > DHA Defence' -> 'DHA Defence'."""
    parts = (path or "-").split(" > ")
    return " > ".join(parts[2:]) if len(parts) > 2 else path or "-"


def scrape_running():
    """True while any run / probe / new-ad check holds the one-scrape-at-a-time lock."""
    return query("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND objid = %(id)s",
                 {"id": store.SCRAPE_LOCK_ID}, one=True)["n"] > 0


def current_check():
    """The new-ad check that's running now, or None. (A row marked running is only really running
    while its process holds the lock; otherwise it was interrupted.)"""
    if not scrape_running():
        return None
    return query("SELECT * FROM new_checks WHERE status = 'running' ORDER BY id DESC LIMIT 1", one=True)


@app.route("/")
def overview():
    city = selected_city()
    cf, cp = city_filter()
    totals = query(f"""
        SELECT count(*) FILTER (WHERE is_active) AS active,
               count(*) FILTER (WHERE is_active AND purpose = 'for-sale') AS for_sale,
               count(*) FILTER (WHERE is_active AND purpose = 'for-rent') AS for_rent,
               count(*) FILTER (WHERE is_active AND verified) AS verified,
               count(*) FILTER (WHERE zameen_created >= now() - interval '24 hours') AS last_24h,
               count(DISTINCT agency_id) AS agencies, count(DISTINCT location_id) AS areas
        FROM listings WHERE {cf}
    """, cp, one=True)

    # what Zameen says each city's category has: the latest pass's count, else the latest probe's
    probes = {(r["city"], r["category_key"]): r["total_hits"] for r in query("""
        SELECT DISTINCT ON (city, category_key) city, category_key, total_hits FROM category_probes
        WHERE ok ORDER BY city, category_key, id DESC
    """)}
    last_pass = {(r["city"], r["category_key"]): r for r in query("""
        SELECT DISTINCT ON (city, category_key) city, category_key, status, expected_total, finished_at
        FROM crawl_passes ORDER BY city, category_key, id DESC
    """)}
    saved = {r["category_key"]: r for r in query(f"""
        SELECT category_key, count(*) FILTER (WHERE is_active) AS active,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE is_active)) AS median_price
        FROM listings WHERE {cf} GROUP BY 1
    """, cp)}

    def on_zameen(city_name, key):
        return (last_pass.get((city_name, key)) or {}).get("expected_total") or probes.get((city_name, key))

    # categories table: each category summed over the chosen city (or every city)
    groups = {}
    for c in [city] if city else [c["name"] for c in categories.cities()]:
        for cat in categories.load(city_name=c)[1]:
            row = groups.setdefault(cat.group, {}).setdefault(cat.name, {"name": cat.name})
            cell = row.setdefault("sale" if cat.purpose == "for-sale" else "rent",
                                  {"key": cat.key, "on_zameen": None, "statuses": set()})
            z = on_zameen(c, cat.key)
            if z is not None:
                cell["on_zameen"] = (cell["on_zameen"] or 0) + z
            status = (last_pass.get((c, cat.key)) or {}).get("status")
            if status:
                cell["statuses"].add(status)
    for rows in groups.values():
        for row in rows.values():
            for cell in (row.get("sale"), row.get("rent")):
                if not cell:
                    continue
                s = saved.get(cell["key"]) or {}
                cell.update(active=s.get("active", 0), median_price=s.get("median_price"),
                            status="running" if "running" in cell["statuses"] else
                            "done" if cell["statuses"] else None)
                cell["coverage"] = cell["active"] / cell["on_zameen"] if cell["on_zameen"] else \
                    (1.0 if cell["status"] == "done" else None)
    groups = {g: list(rows.values()) for g, rows in groups.items()}
    zameen_total = sum(cell["on_zameen"] or 0 for rows in groups.values() for r in rows
                       for cell in (r.get("sale"), r.get("rent")) if cell)

    # cities table: one row per city
    per_city = {r["city"]: r for r in query("""
        SELECT city, count(*) FILTER (WHERE is_active) AS active FROM listings GROUP BY 1
    """)}
    finished = {r["city"]: r["finished"] for r in query(
        "SELECT city, max(finished_at) AS finished FROM crawl_passes WHERE status = 'done' GROUP BY 1")}
    city_rows = []
    with db().cursor() as cur:
        paces = {c["name"]: pace.get(cur, c["name"]) for c in categories.cities()}
    for c in categories.cities():
        z = [on_zameen(c["name"], cat.key) for cat in categories.load(city_name=c["name"])[1]]
        z = sum(v for v in z if v) or None
        active = (per_city.get(c["name"]) or {}).get("active", 0)
        city_rows.append({"name": c["name"], "active": active, "on_zameen": z,
                          "coverage": active / z if z and active else None, "finished": finished.get(c["name"]),
                          "pace": paces[c["name"]]})

    running = query("""
        SELECT p.id, p.city, p.category_key, p.started_at, p.pages_fetched, p.errors, p.expected_total,
               (SELECT count(*) FROM listings l WHERE l.last_pass_id = p.id) AS seen,
               count(t.*) AS areas, count(t.*) FILTER (WHERE t.status <> 'pending') AS areas_done
        FROM crawl_passes p LEFT JOIN crawl_tasks t ON t.pass_id = p.id
        WHERE p.status = 'running' GROUP BY p.id ORDER BY p.id DESC
    """)
    passes = query(f"""
        SELECT id, city, category_key, status, started_at, finished_at, expected_total, pages_fetched, errors, note,
               (SELECT count(*) FROM listings l WHERE l.last_pass_id = p.id) AS saved
        FROM crawl_passes p WHERE {cf} ORDER BY id DESC LIMIT 12
    """, cp)
    checks = query("SELECT * FROM new_checks ORDER BY id DESC LIMIT 8")
    running_check = current_check()
    auto = {**task_info(), "paused": schedule.is_paused(db()), "every": schedule.EVERY_HOURS,
            "enabled": config.new_checks_enabled(),
            "cities": len(categories.cities())}
    return render_template("overview.html", totals=totals, groups=groups, zameen_total=zameen_total,
                           city_rows=city_rows, running=running, running_check=running_check, passes=passes,
                           checks=checks, auto=auto)


@app.post("/auto-check")
def auto_check():
    """Pause / resume button for the scheduled new-ad checks."""
    schedule.set_paused(db(), request.form.get("action") == "pause")
    return redirect(url_for("overview", city=selected_city()) + "#auto")


def category_chips(purpose):
    """Groups of type buttons for the listings page, with active-ad counts for the chosen purpose and city."""
    cf, cp = city_filter()
    counts = {r["category_key"]: r["n"] for r in query(
        f"SELECT category_key, count(*) AS n FROM listings WHERE is_active AND {cf} GROUP BY 1", cp)}
    groups = {}
    for c in categories.load()[1]:
        if purpose and c.purpose != purpose:
            continue
        g = groups.setdefault(c.group, {"name": c.group, "count": 0, "types": {}})
        t = g["types"].setdefault(c.slug, {"slug": c.slug, "name": c.name, "count": 0})
        t["count"] += counts.get(c.key, 0)
        g["count"] += counts.get(c.key, 0)
    return [{**g, "types": list(g["types"].values())} for g in groups.values()]


@app.route("/listings")
def listings():
    a = request.args.to_dict()
    if "/" in a.get("category", ""):          # links from the overview: for-sale/Houses_Property
        a["purpose"], a["type"] = a.pop("category").split("/", 1)
    if a.get("purpose") not in ("for-sale", "for-rent"):
        a.pop("purpose", None)
    cf, params = city_filter("l.city")
    where = [cf]
    if a.get("purpose"):
        where.append("l.purpose = %(purpose)s")
        params["purpose"] = a["purpose"]
    if a.get("type"):
        where.append("split_part(category_key, '/', 2) = %(type)s")
        params["type"] = a["type"]
    elif a.get("group"):
        where.append("category_group = %(group)s")
        params["group"] = a["group"]
    if a.get("area"):
        where.append("location_path ILIKE %(area)s")
        params["area"] = f"%{a['area']}%"
    if a.get("q"):
        where.append("(title ILIKE %(q)s OR contact_name ILIKE %(q)s OR ag.name ILIKE %(q)s)")
        params["q"] = f"%{a['q']}%"
    for field, op, col in (("min_price", ">=", "price"), ("max_price", "<=", "price"),
                           ("beds", ">=", "beds"), ("min_marla", ">=", "area_marla"),
                           ("max_marla", "<=", "area_marla")):
        try:
            params[field] = float(a[field])
            where.append(f"{col} {op} %({field})s")
        except (KeyError, ValueError):
            pass
    if a.get("verified"):
        where.append("verified")
    if a.get("posted") in POSTED:
        where.append("l.zameen_created >= now() - %(hours)s * interval '1 hour'")
        params["hours"] = POSTED[a["posted"]][1]
    if a.get("show") != "all":
        where.append("is_active")

    sort = a.get("sort") if a.get("sort") in SORTS else "newest"
    try:
        page = max(int(a.get("page", 1)), 1)
    except ValueError:
        page = 1
    sql_where = " AND ".join(where)
    tables = "listings l LEFT JOIN agencies ag ON ag.id = l.agency_id"
    total = query(f"SELECT count(*) AS n FROM {tables} WHERE {sql_where}", params, one=True)["n"]
    rows = query(f"""
        SELECT l.id, l.city, category_key, title, price, area_marla, beds, baths, location_path, ag.name AS agency_name,
               contact_name, mobile, l.zameen_created, is_active, verified, listing_tier, url,
               round(price / NULLIF(area_marla, 0)) AS per_marla
        FROM {tables} WHERE {sql_where}
        ORDER BY {SORTS[sort][1]}, l.id LIMIT {PER_PAGE} OFFSET %(offset)s
    """, {**params, "offset": (page - 1) * PER_PAGE})

    def link(**changes):
        """This page's address with some filters changed (None removes one); always back to page 1."""
        q = {k: v for k, v in a.items() if k != "page"}
        for k, v in changes.items():
            if v is None:
                q.pop(k, None)
            else:
                q[k] = v
        return url_for("listings", **q)

    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    filtered = any(a.get(k) for k in a if k not in ("page", "sort", "purpose", "type", "group", "city"))

    # the filters in use, each with a link that removes it (the "×" chips above the results)
    def money(v):
        try:
            return pkr(int(float(v)))
        except ValueError:
            return v
    active = [(label, link(**{k: None})) for k, label in (
        ("area", f"Area: {a.get('area')}"), ("q", f"“{a.get('q')}”"),
        ("min_price", f"From {money(a.get('min_price', '0'))}"), ("max_price", f"Up to {money(a.get('max_price', '0'))}"),
        ("min_marla", f"From {a.get('min_marla')} marla"), ("max_marla", f"Up to {a.get('max_marla')} marla"),
        ("beds", f"{a.get('beds')}+ beds"), ("posted", POSTED.get(a.get("posted"), ("",))[0]),
        ("verified", "Verified only"), ("show", "Including ads gone from Zameen")) if a.get(k)]
    more_open = any(a.get(k) for k in ("min_marla", "max_marla", "beds", "posted", "verified", "show", "q"))
    return render_template("listings.html", rows=rows, total=total, page=page, pages=pages, args=a,
                           sorts=SORTS, sort=sort, posted=POSTED, chips=category_chips(a.get("purpose")),
                           filtered=filtered, link=link, active=active, more_open=more_open,
                           page_links=page_numbers(page, pages), per_page=PER_PAGE)


def page_numbers(page, pages):
    """Page numbers for the pager: first, last, and two either side of this one; None where there's a gap (…)."""
    out = []
    for n in sorted({1, pages, *range(max(page - 2, 1), min(page + 2, pages) + 1)}):
        if out and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


def page_arg(a):
    try:
        return max(int(a.get("page", 1)), 1)
    except ValueError:
        return 1


# "New listings" periods: label and how many days back from today (0 = today only), by the day we found the ad
NEW_PERIODS = {"today": ("Today", 0), "3d": ("Last 3 days", 2), "7d": ("Last 7 days", 6), "30d": ("Last 30 days", 29)}
# A genuinely new ad: found by a new-ad check, or found by a full scrape within 2 days of being posted. (A city's
# first full scrape saved every ad on Zameen that day; those older ads aren't "new" and are left out.)
GENUINELY_NEW = "(l.first_check_id IS NOT NULL OR l.zameen_created >= l.first_seen - interval '2 days')"


@app.template_filter("day_word")
def day_word(day):
    """A date as 'today', 'yesterday', '3 days ago' or '12 Oct' (Pakistan dates)."""
    if not day:
        return "-"
    days = (config.today() - day).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago" if days < 7 else day.strftime("%d %b")


@app.route("/new")
def new_listings():
    """Newly found ads: by period (the day we first saved them) or by one new-ad check; a feed of cards."""
    a = request.args.to_dict()
    checks = query("SELECT id, started_at, finished_at, status, new_listings, pages_fetched, errors, scheduled, "
                   "cities, note FROM new_checks ORDER BY id DESC LIMIT 30")
    running = current_check()
    if a.get("purpose") not in ("for-sale", "for-rent"):
        a.pop("purpose", None)
    by_check = None
    try:
        by_check = int(a["check"])
    except (KeyError, ValueError):
        a.pop("check", None)
    if by_check is None and a.get("period") not in NEW_PERIODS:
        a["period"] = "3d"

    cf, params = city_filter("l.city")
    where = ["l.is_active", cf]
    if by_check is not None:
        where.append("l.first_check_id = %(check)s")
        params["check"] = by_check
        a.pop("period", None)
    else:
        where += [GENUINELY_NEW, "l.first_seen >= %(since)s"]
        params["since"] = config.today() - timedelta(days=NEW_PERIODS[a["period"]][1])
    if a.get("purpose"):
        where.append("l.purpose = %(purpose)s")
        params["purpose"] = a["purpose"]
    sql_where = " AND ".join(where)
    counts = {r["category_key"]: r["n"] for r in query(
        f"SELECT category_key, count(*) AS n FROM listings l WHERE {sql_where} GROUP BY 1", params)}
    per_day = query(f"SELECT first_seen AS day, count(*) AS n FROM listings l WHERE {sql_where} "
                    f"GROUP BY 1 ORDER BY 1 DESC", params) if by_check is None else []

    # category buttons, in categories.json order, only those with new ads
    chips = {}
    for c in categories.load()[1]:
        if counts.get(c.key):
            # "Houses" when sale/rent is chosen; "Houses for sale" when both are shown
            chips.setdefault(c.group, []).append({"key": c.key, "label": c.name if a.get("purpose") else c.label,
                                                  "count": counts[c.key]})
    category = a.get("category") if a.get("category") in counts else None
    if category:
        sql_where += " AND l.category_key = %(category)s"
        params["category"] = category
    total = counts.get(category, 0) if category else sum(counts.values())

    page = page_arg(a)
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    rows = query(f"""
        SELECT l.id, l.city, l.category_key, l.title, l.price, l.area_marla, l.beds, l.baths, l.location_path,
               l.contact_name, l.mobile, l.zameen_created, l.first_seen, l.verified, l.listing_tier, l.url,
               l.is_active, ag.name AS agency_name, round(l.price / NULLIF(l.area_marla, 0)) AS per_marla
        FROM listings l LEFT JOIN agencies ag ON ag.id = l.agency_id
        WHERE {sql_where}
        ORDER BY l.first_seen DESC, l.zameen_created DESC NULLS LAST, l.id LIMIT {PER_PAGE} OFFSET %(offset)s
    """, {**params, "offset": (page - 1) * PER_PAGE})

    def link(**changes):
        q = {k: v for k, v in a.items() if k not in ("page", "starting")}
        q.update(changes)
        return url_for("new_listings", **{k: v for k, v in q.items() if v is not None})

    busy = scrape_running()
    last = next((c for c in checks if c["status"] != "running"), None)
    return render_template("new.html", checks=checks, args=a, chips=chips, total=total, all_total=sum(counts.values()),
                           rows=rows, category=category, page=page, pages=pages, page_links=page_numbers(page, pages),
                           per_page=PER_PAGE, link=link, running=running, last=last, periods=NEW_PERIODS,
                           by_check=by_check, per_day=per_day,
                           other_scrape=busy and not running, starting=bool(a.get("starting")) and not busy,
                           stopping=bool(running) and str(running["id"]) == stop_requested())


def stop_requested():
    return (query("SELECT value FROM settings WHERE key = 'stop_check'", one=True) or {}).get("value")


@app.post("/new/start")
def start_check():
    """"Check for new ads now" button: start a check in the background, unless any scrape is running."""
    if not scrape_running():
        schedule.start_check_now()
        return redirect(url_for("new_listings", starting=1, city=selected_city()))
    return redirect(url_for("new_listings", city=selected_city()))


@app.post("/new/stop")
def stop_check():
    """Stop button: the running check stops after its current page, or within seconds while it's waiting to
    retry a refused page (fetcher.should_stop)."""
    running = current_check()
    if running:
        with db().cursor() as cur:
            store.set_setting(cur, "stop_check", str(running["id"]))
    return redirect(url_for("new_listings", city=selected_city()))


@app.route("/listing/<int:listing_id>")
def listing(listing_id):
    l = query("SELECT * FROM listings WHERE id = %(id)s", {"id": listing_id}, one=True)
    if not l:
        abort(404)
    prices = query("SELECT price, seen_on FROM price_history WHERE listing_id = %(id)s ORDER BY seen_on",
                   {"id": listing_id})
    changes = query("SELECT field, old_value, new_value, changed_on FROM listing_changes "
                    "WHERE listing_id = %(id)s ORDER BY changed_on DESC, id DESC", {"id": listing_id})
    agency = query("SELECT * FROM agencies WHERE id = %(id)s", {"id": l["agency_id"]}, one=True) \
        if l["agency_id"] else None
    if agency:
        agency["ads"] = query("SELECT count(*) AS n FROM listings WHERE agency_id = %(a)s AND is_active",
                              {"a": l["agency_id"]}, one=True)["n"]
    # how we found it and last saw it: the full pass and/or the new-ad check
    seen_pass = query("SELECT id, started_at FROM crawl_passes WHERE id = %(p)s", {"p": l["last_pass_id"]}, one=True) \
        if l["last_pass_id"] else None
    found_check = query("SELECT id, started_at FROM new_checks WHERE id = %(c)s", {"c": l["first_check_id"]}, one=True) \
        if l["first_check_id"] else None
    # the URL changes with the title on every edit: it only repeats the title change, so it isn't listed
    edits = len(changes)
    changes = [c for c in changes if c["field"] != "url"]
    market, similar = compare_with_similar(l)
    raw = json.dumps(l["raw"], indent=2, ensure_ascii=False, sort_keys=True) if l["raw"] else None
    return render_template("listing.html", l=l, prices=prices, changes=changes, agency=agency, edits=edits,
                           market=market, similar=similar, seen_pass=seen_pass, found_check=found_check, raw=raw)


MIN_TO_COMPARE = 5   # fewer similar ads than this: no comparison (it would mean little)


def compare_with_similar(l):
    """How this ad's price per marla compares with active ads of the same type, in the same area, of a similar
    size (half to double), and up to 6 of those ads. Uses the smallest area with enough ads: the sub-area
    ('DHA Phase 5 > Sector B') if it has MIN_TO_COMPARE, else the area ('DHA Phase 5')."""
    if not (l["price"] and l["area_marla"] and l["location_path"]):
        return None, []
    parts = l["location_path"].split(" > ")
    base = {"id": l["id"], "city": l["city"], "cat": l["category_key"], "size": l["area_marla"]}
    for depth in range(len(parts), 2, -1):       # deepest first; never wider than the area under the city
        where_path = " > ".join(parts[:depth])
        p = {**base, "path": where_path, "prefix": where_path + " > %"}
        cond = """city = %(city)s AND category_key = %(cat)s AND is_active AND id <> %(id)s
                  AND price > 0 AND area_marla BETWEEN %(size)s / 2 AND %(size)s * 2
                  AND (location_path = %(path)s OR location_path LIKE %(prefix)s)"""
        m = query(f"""SELECT count(*) AS n,
                             percentile_cont(0.5) WITHIN GROUP (ORDER BY price / area_marla) AS median,
                             percentile_cont(0.25) WITHIN GROUP (ORDER BY price / area_marla) AS low,
                             percentile_cont(0.75) WITHIN GROUP (ORDER BY price / area_marla) AS high
                      FROM listings WHERE {cond}""", p, one=True)
        if m["n"] >= MIN_TO_COMPARE:
            mine = float(l["price"]) / float(l["area_marla"])
            m.update(area=parts[depth - 1], mine=mine, diff=mine / m["median"] - 1)
            similar = query(f"""SELECT id, title, price, area_marla, beds, baths, location_path, zameen_created,
                                       round(price / area_marla) AS per_marla
                                FROM listings WHERE {cond}
                                ORDER BY abs(area_marla - %(size)s), zameen_created DESC LIMIT 6""", p)
            return m, similar
    return None, []


def run(port=5000):
    app.run(host="127.0.0.1", port=port, debug=False)
