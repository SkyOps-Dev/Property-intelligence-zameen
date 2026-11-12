"""STORE: everything that reads or writes the database."""
import os
from datetime import datetime
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import Json

from . import config

SCHEMA_FILE = os.path.join(config.ROOT, "db", "schema.sql")

# Columns written from a parsed listing (insert and update)
LISTING_COLUMNS = [
    "object_id", "purpose", "category_group", "type", "type_slug", "title", "title_ur",
    "short_description", "price", "remaining_installments", "beds", "baths",
    "area_sqm", "area_sqft", "area_marla", "location_id", "location_path", "lat", "lng",
    "exact_geography", "agency_id", "contact_name", "mobile", "phone", "whatsapp", "verified",
    "listing_tier", "url", "zameen_created", "zameen_updated",
]

# Edits worth remembering (price has its own table)
TRACKED_FIELDS = ["title", "type", "short_description", "beds", "baths", "area_sqft", "agency_id",
                  "contact_name", "mobile", "phone", "whatsapp", "verified", "listing_tier",
                  "url"]


def connect(url=None):
    """A database connection that shows times in Pakistan time (they're stored in UTC either way)."""
    conn = psycopg2.connect(url or config.DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE %s", (config.DISPLAY_TZ,))
    conn.commit()
    return conn


def ensure_database(url=None):
    """Create the database itself if it doesn't exist yet (connects to the 'postgres' database to do it)."""
    url = url or config.DATABASE_URL
    name = urlparse(url).path.lstrip("/")
    admin = psycopg2.connect(url.rsplit("/", 1)[0] + "/postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if cur.fetchone():
                return False
            cur.execute(f'CREATE DATABASE "{name}"')
            return True
    finally:
        admin.close()


def init_db(conn):
    with open(SCHEMA_FILE, encoding="utf-8") as f, conn.cursor() as cur:
        cur.execute(f.read())
    conn.commit()


# ---------- saving a page ----------

def upsert_locations(cur, locations):
    # parents first (lower level), so the foreign key is always satisfied
    for loc in sorted(locations, key=lambda l: l["level"] or 0):
        cur.execute("""
            INSERT INTO locations (id, name, name_ur, slug, level, parent_id)
            VALUES (%(id)s, %(name)s, %(name_ur)s, %(slug)s, %(level)s, %(parent_id)s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, name_ur = EXCLUDED.name_ur, slug = EXCLUDED.slug,
                level = EXCLUDED.level, parent_id = EXCLUDED.parent_id
        """, loc)


def upsert_agencies(cur, agencies, today=None):
    today = today or config.today()
    for a in agencies:
        cur.execute("""
            INSERT INTO agencies (id, name, slug, product, tier, product_score, zameen_created, first_seen, last_seen)
            VALUES (%(id)s, %(name)s, %(slug)s, %(product)s, %(tier)s, %(product_score)s, %(zameen_created)s,
                    %(today)s, %(today)s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, slug = EXCLUDED.slug, product = EXCLUDED.product, tier = EXCLUDED.tier,
                product_score = EXCLUDED.product_score, last_seen = EXCLUDED.last_seen
        """, {**a, "today": today})


def save_listing(cur, listing, city, category_key, pass_id=None, today=None, check_id=None):
    """Insert or update one listing found in `city`'s search. Returns 'new', 'changed' (price) or 'same'.
    Price changes go to price_history, other edits to listing_changes.
    Without a pass_id (quick check for new ads) the listing keeps the last full pass that saw it.
    check_id: the new-ad check that's saving it, remembered only when the listing is new."""
    today = today or config.today()
    cur.execute(f"SELECT price, {', '.join(TRACKED_FIELDS)} FROM listings WHERE id = %s", (listing["id"],))
    row = cur.fetchone()

    cols = ["id", *LISTING_COLUMNS, "city", "category_key", "first_seen", "last_seen", "last_pass_id", "is_active",
            "raw", "first_check_id"]
    values = {**listing, "city": city, "category_key": category_key, "first_seen": today, "last_seen": today,
              "last_pass_id": pass_id, "is_active": True, "raw": Json(listing["raw"]), "first_check_id": check_id}
    updates = [f"{c} = EXCLUDED.{c}" for c in cols
               if c not in ("id", "first_seen", "last_pass_id", "first_check_id")]
    updates.append("last_pass_id = COALESCE(EXCLUDED.last_pass_id, listings.last_pass_id)")
    cur.execute(
        f"INSERT INTO listings ({', '.join(cols)}) VALUES ({', '.join(f'%({c})s' for c in cols)}) "
        f"ON CONFLICT (id) DO UPDATE SET {', '.join(updates)}",
        values,
    )

    old_price = row[0] if row else None
    if (row is None or old_price != listing["price"]) and listing["price"] is not None:
        cur.execute("INSERT INTO price_history (listing_id, price, seen_on) VALUES (%s, %s, %s)",
                    (listing["id"], listing["price"], today))
    if row is not None:
        for field, old in zip(TRACKED_FIELDS, row[1:]):
            new = listing[field]
            if old != new:
                cur.execute(
                    "INSERT INTO listing_changes (listing_id, field, old_value, new_value, changed_on) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (listing["id"], field, None if old is None else str(old),
                     None if new is None else str(new), today))
    if row is None:
        return "new"
    return "changed" if old_price != listing["price"] else "same"


# ---------- passes and tasks (stop / resume) ----------

def open_pass(cur, city, category_key):
    """Resume this city's unfinished pass of the category, or start a new one. Returns (pass_id, resumed)."""
    cur.execute("""
        SELECT id FROM crawl_passes WHERE city = %s AND category_key = %s AND status IN ('running', 'paused')
        ORDER BY id DESC LIMIT 1
    """, (city, category_key))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE crawl_passes SET status = 'running' WHERE id = %s", (row[0],))
        return row[0], True
    cur.execute("INSERT INTO crawl_passes (city, category_key) VALUES (%s, %s) RETURNING id", (city, category_key))
    return cur.fetchone()[0], False


def add_task(cur, pass_id, slug, name, parent_slug=None, depth=0):
    cur.execute("""
        INSERT INTO crawl_tasks (pass_id, location_slug, location_name, parent_slug, depth)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, (pass_id, slug, name, parent_slug, depth))


def next_task(cur, pass_id):
    """The next area to work on: biggest areas first (lowest depth), then by name."""
    cur.execute("""
        SELECT location_slug, location_name, depth, total_pages, last_page FROM crawl_tasks
        WHERE pass_id = %s AND status = 'pending' ORDER BY depth, location_slug LIMIT 1
    """, (pass_id,))
    row = cur.fetchone()
    return dict(zip(("slug", "name", "depth", "total_pages", "last_page"), row)) if row else None


def update_task(cur, pass_id, slug, **fields):
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    cur.execute(f"UPDATE crawl_tasks SET {sets}, updated_at = now() WHERE pass_id = %(pass_id)s "
                f"AND location_slug = %(slug)s", {**fields, "pass_id": pass_id, "slug": slug})


def add_pass_counts(cur, pass_id, pages=0, new=0, changed=0, errors=0):
    cur.execute("""
        UPDATE crawl_passes SET pages_fetched = pages_fetched + %s, new_listings = new_listings + %s,
               price_changes = price_changes + %s, errors = errors + %s
        WHERE id = %s
    """, (pages, new, changed, errors, pass_id))


def set_pass(cur, pass_id, **fields):
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    cur.execute(f"UPDATE crawl_passes SET {sets} WHERE id = %(id)s", {**fields, "id": pass_id})


def pass_summary(cur, pass_id):
    cur.execute("""
        SELECT p.category_key, p.expected_total, p.started_at,
               (SELECT count(*) FROM listings l WHERE l.last_pass_id = p.id) AS saved,
               (SELECT count(*) FROM crawl_tasks t WHERE t.pass_id = p.id AND t.status = 'failed') AS failed
        FROM crawl_passes p WHERE p.id = %s
    """, (pass_id,))
    return dict(zip(("category_key", "expected_total", "started_at", "saved", "failed"), cur.fetchone()))


def mark_inactive(cur, city, category_key, pass_id):
    """After a complete pass: this city's listings of the category that the pass didn't see are gone
    from Zameen. Only this city's: a pass never touches another city's ads."""
    cur.execute("""
        UPDATE listings SET is_active = FALSE
        WHERE city = %s AND category_key = %s AND is_active AND last_pass_id IS DISTINCT FROM %s
    """, (city, category_key, pass_id))
    return cur.rowcount


SCRAPE_LOCK_ID = 728200


def take_scrape_lock(conn):
    """One scrape at a time: True if no other run / probe / new-ad check is going on.
    PostgreSQL drops the lock by itself when the process ends, even after a crash."""
    with conn, conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (SCRAPE_LOCK_ID,))
        return cur.fetchone()[0]


# ---------- quick checks for new ads ----------

def open_check(cur, categories, scheduled, cities):
    # a check that is still 'running' was interrupted (the lock guarantees nothing else is running)
    cur.execute("UPDATE new_checks SET status = 'stopped', note = 'interrupted' WHERE status = 'running'")
    cur.execute("INSERT INTO new_checks (categories, scheduled, cities) VALUES (%s, %s, %s) RETURNING id",
                (categories, scheduled, ", ".join(cities)))
    return cur.fetchone()[0]


def add_check_counts(cur, check_id, pages=0, new=0, changed=0, errors=0):
    cur.execute("""
        UPDATE new_checks SET pages_fetched = pages_fetched + %s, new_listings = new_listings + %s,
               price_changes = price_changes + %s, errors = errors + %s
        WHERE id = %s
    """, (pages, new, changed, errors, check_id))


def set_check(cur, check_id, **fields):
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    cur.execute(f"UPDATE new_checks SET {sets} WHERE id = %(id)s", {**fields, "id": check_id})


def check_marker(city, category_key):
    """settings key holding when this city's category was last checked for new ads."""
    return f"new_check:{city}:{category_key}"


def last_checked(cur, city, category_key):
    """Since when this city's category is known complete: the later of its last new-ad check and the start
    of its last full pass (a full pass read everything, so a check after it needn't read back further);
    None if neither (never scraped)."""
    marker = get_setting(cur, check_marker(city, category_key))
    if marker:
        marker = datetime.fromisoformat(marker)
        # markers saved before 2026-10-28 have no zone: they were Pakistan time
        marker = marker if marker.tzinfo else marker.replace(tzinfo=config.PAKISTAN)
    cur.execute("SELECT max(started_at) FROM crawl_passes WHERE city = %s AND category_key = %s AND status = 'done'",
                (city, category_key))
    full_pass = cur.fetchone()[0]
    return max((m for m in (marker, full_pass) if m), default=None)


def get_setting(cur, key, default=None):
    cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(cur, key, value):
    cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s",
                (key, value, value))


def save_probe(cur, city, category_key, url, ok, total_hits=None, total_pages=None, sub_locations=None, note=None):
    cur.execute("""
        INSERT INTO category_probes (city, category_key, url, ok, total_hits, total_pages, sub_locations, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (city, category_key, url, ok, total_hits, total_pages, sub_locations, note))
