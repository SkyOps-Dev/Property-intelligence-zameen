-- Zameen Scrapper: database schema (PostgreSQL). Safe to run again: python main.py init-db

-- 1. Area tree: Pakistan > Islamabad Capital > Islamabad > I-11 > I-11/2
CREATE TABLE IF NOT EXISTS locations (
    id          BIGINT PRIMARY KEY,              -- Zameen externalID
    name        TEXT NOT NULL,
    name_ur     TEXT,                            -- Urdu name
    slug        TEXT,                            -- e.g. /Islamabad_I_11-706 (used in search addresses)
    level       INT,                             -- 0 country, 1 province, 2 city, 3 sector, 4+ sub-sector/block
    parent_id   BIGINT REFERENCES locations(id)
);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations (parent_id);

-- 2. Agencies (property dealers' companies)
CREATE TABLE IF NOT EXISTS agencies (
    id              BIGINT PRIMARY KEY,          -- Zameen agency externalID
    name            TEXT,
    slug            TEXT,
    product         TEXT,                        -- titanium plus / titanium / ...
    tier            INT,
    product_score   INT,
    zameen_created  DATE,
    first_seen      DATE NOT NULL,
    last_seen       DATE NOT NULL
);

-- 3. One row per property ad, updated on every pass
CREATE TABLE IF NOT EXISTS listings (
    id                  BIGINT PRIMARY KEY,      -- Zameen externalID (stable per ad)
    object_id           BIGINT,                  -- Zameen internal id
    category_key        TEXT NOT NULL,           -- e.g. for-rent/Houses_Property (which pass found it)
    purpose             TEXT,                    -- for-sale / for-rent
    category_group      TEXT,                    -- Homes / Plots / Commercial
    type                TEXT,                    -- Houses, Flats, Residential Plots ...
    type_slug           TEXT,
    title               TEXT,
    title_ur            TEXT,                    -- Urdu title
    short_description   TEXT,                    -- first ~100 characters of the description
    price               BIGINT,                  -- PKR
    remaining_installments INT,
    beds                INT,
    baths               INT,
    area_sqm            NUMERIC(12,2),
    area_sqft           INT,
    area_marla          NUMERIC(10,2),           -- 1 marla = 225 sq ft
    location_id         BIGINT REFERENCES locations(id),
    location_path       TEXT,                    -- Islamabad Capital > Islamabad > I-11 > I-11/2
    lat                 DOUBLE PRECISION,
    lng                 DOUBLE PRECISION,
    exact_geography     BOOLEAN,
    agency_id           BIGINT REFERENCES agencies(id),
    contact_name        TEXT,                    -- dealer / agent name on the ad
    mobile              TEXT,                    -- comma-separated
    phone               TEXT,                    -- landline(s), comma-separated
    whatsapp            TEXT,
    verified            BOOLEAN,                 -- Zameen's own "verified" badge
    listing_tier        TEXT,                    -- hot / superhot / ...
    url                 TEXT,
    zameen_created      TIMESTAMPTZ,
    zameen_updated      TIMESTAMPTZ,
    -- our own tracking
    first_seen          DATE NOT NULL,
    last_seen           DATE NOT NULL,
    last_pass_id        BIGINT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    raw                 JSONB                    -- full original record minus pictures
);
-- which new-ad check first found the listing (empty: found by a full pass)
ALTER TABLE listings ADD COLUMN IF NOT EXISTS first_check_id BIGINT;
-- photo/video counts were stored at first; removed 2026-10-26 (not needed)
ALTER TABLE listings DROP COLUMN IF EXISTS photo_count, DROP COLUMN IF EXISTS video_count;
-- removed 2026-11-10 (user's choice): always empty (Zameen sends them blank, or reserved for ad detail pages
-- that weren't built) or always the same value. Zameen's own values are still in `raw`.
ALTER TABLE listings DROP COLUMN IF EXISTS rent_frequency, DROP COLUMN IF EXISTS permit_number,
                     DROP COLUMN IF EXISTS reference_number, DROP COLUMN IF EXISTS has_tour,
                     DROP COLUMN IF EXISTS zameen_state, DROP COLUMN IF EXISTS description,
                     DROP COLUMN IF EXISTS features, DROP COLUMN IF EXISTS details_fetched_on;
ALTER TABLE locations DROP COLUMN IF EXISTS lat, DROP COLUMN IF EXISTS lng;
CREATE INDEX IF NOT EXISTS idx_listings_category ON listings (category_key, is_active);
CREATE INDEX IF NOT EXISTS idx_listings_last_pass ON listings (last_pass_id);          -- "saved by this pass" counts
CREATE INDEX IF NOT EXISTS idx_listings_first_check ON listings (first_check_id) WHERE first_check_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listings_created ON listings (zameen_created DESC);     -- newest first, "posted in last 24 h"
CREATE INDEX IF NOT EXISTS idx_listings_location ON listings (location_id);
CREATE INDEX IF NOT EXISTS idx_listings_agency ON listings (agency_id);
CREATE INDEX IF NOT EXISTS idx_listings_search ON listings (purpose, type, is_active, price);

-- 4. Price over time: a row when a listing is first seen and whenever its price changes
CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL REFERENCES listings(id),
    price       BIGINT NOT NULL,
    seen_on     DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history (listing_id, seen_on);

-- 5. Every other edit to an ad: one row per changed field
CREATE TABLE IF NOT EXISTS listing_changes (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL REFERENCES listings(id),
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_on  DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_changes_listing ON listing_changes (listing_id, changed_on);

-- 6. One "pass" = one complete scrape of one category. Can be stopped and resumed.
CREATE TABLE IF NOT EXISTS crawl_passes (
    id              BIGSERIAL PRIMARY KEY,
    category_key    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',   -- running / paused / done / failed
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    expected_total  INT,          -- what Zameen says the category has (whole city)
    listings_saved  INT DEFAULT 0, -- distinct listings this pass saved
    new_listings    INT DEFAULT 0,
    price_changes   INT DEFAULT 0,
    pages_fetched   INT DEFAULT 0,
    errors          INT DEFAULT 0,
    marked_inactive INT DEFAULT 0,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_passes_category ON crawl_passes (category_key, status);

-- 7. The areas a pass has to go through, and how far it got in each (for resuming)
CREATE TABLE IF NOT EXISTS crawl_tasks (
    pass_id         BIGINT NOT NULL REFERENCES crawl_passes(id),
    location_slug   TEXT NOT NULL,                     -- /Islamabad-3, /Islamabad_I_11-706 ...
    location_name   TEXT,
    parent_slug     TEXT,
    depth           INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending / done / split / failed
    total_hits      INT,
    total_pages     INT,
    last_page       INT NOT NULL DEFAULT 0,            -- last page saved; resume from the next one
    capped          BOOLEAN NOT NULL DEFAULT FALSE,    -- old (we thought Zameen stopped at 1,000 ads); no longer set
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pass_id, location_slug)
);

-- 8. Quick checks for new ads ('python main.py new'): one row per check, across all its categories
CREATE TABLE IF NOT EXISTS new_checks (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',   -- running / done / stopped / failed
    scheduled       BOOLEAN NOT NULL DEFAULT FALSE,    -- started by the 6-hourly schedule (not by hand)
    categories      INT,
    pages_fetched   INT DEFAULT 0,
    new_listings    INT DEFAULT 0,
    price_changes   INT DEFAULT 0,
    errors          INT DEFAULT 0,
    note            TEXT
);

-- 9. Switches, e.g. auto_check = on / off (pause the scheduled checks)
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- 10. Results of 'python main.py probe': size of each category, and whether its address works
CREATE TABLE IF NOT EXISTS category_probes (
    id              BIGSERIAL PRIMARY KEY,
    category_key    TEXT NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    url             TEXT,
    ok              BOOLEAN NOT NULL,
    total_hits      INT,
    total_pages     INT,
    sub_locations   INT,
    note            TEXT
);

-- 11. Multi-city (2026-10-28): which city each ad, pass and probe belongs to.
-- Rows from before this were all Islamabad. The default only fills those old rows, then it's dropped,
-- so new rows must always say their city.
-- listings.city = the city whose search last found the ad (an ad near a border can show up in two).
ALTER TABLE listings       ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT 'Islamabad';
ALTER TABLE listings       ALTER COLUMN city DROP DEFAULT;
ALTER TABLE crawl_passes   ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT 'Islamabad';
ALTER TABLE crawl_passes   ALTER COLUMN city DROP DEFAULT;
ALTER TABLE category_probes ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT 'Islamabad';
ALTER TABLE category_probes ALTER COLUMN city DROP DEFAULT;
ALTER TABLE new_checks     ADD COLUMN IF NOT EXISTS cities TEXT;          -- names of the cities it checked
CREATE INDEX IF NOT EXISTS idx_listings_city ON listings (city, category_key, is_active);
CREATE INDEX IF NOT EXISTS idx_crawl_passes_city ON crawl_passes (city, category_key, status);
-- "last checked" markers were 'new_check:<category>' (Islamabad); now 'new_check:<city>:<category>'
UPDATE settings SET key = 'new_check:Islamabad:' || substr(key, 11) WHERE key ~ '^new_check:[^:]+$';

-- 12. Time zones (2026-10-28): every date-time is stored WITH its time zone (PostgreSQL keeps it in UTC),
-- so it's right on any server. Columns created before this were Pakistan local time without a zone: they
-- are converted once (a column that already has a zone is skipped, so running this again changes nothing).
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema = current_schema() AND data_type = 'timestamp without time zone'
    LOOP
        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''Asia/Karachi''',
                       c.table_name, c.column_name, c.column_name);
    END LOOP;
END $$;
