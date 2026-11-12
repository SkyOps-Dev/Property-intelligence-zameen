# CLAUDE.md: start here

Project name: **Zameen Scrapper** (renamed 2026-11-11, user's choice: viewer, README, Windows task). The folder
`zameen-islamabad` and database `zameen_islamabad` keep their names on purpose (user chose names only).
Viewer redesign 2026-11-11 (user's request): Listings = search bar + "More filters", removable filter pills,
ad cards (whole card clickable), numbered pager, ALL-CAPS titles softened (`nice_title`), "posted 5 h ago"
(`ago`). Ad page = hero with price, fact tiles, **Price check** (per-marla vs median of active same-type ads,
half-to-double size, deepest area with ≥5 ads; `compare_with_similar` in web/app.py), similar ads, OSM map,
copy-number buttons; URL rows hidden from "Changes". Note: the map loads tiles from openstreetmap.org.
New listings page redesigned 2026-11-11: period switch (Today / 3 / 7 / 30 days, default 3, by `first_seen`) or
one check; "genuinely new" = found by a check OR by a full scrape within 2 days of being posted (`GENUINELY_NEW`,
so a city's first full scrape doesn't flood it); per-day counts, category chips, the same ad cards as Listings
(shared `_ad_card.html` macros + card CSS in base.html, `page_numbers()` / `page_arg()` helpers), last-check
status + a warning when Zameen refused every page. User changed config UA to Firefox themselves (their call).

⛔ **2026-11-11: Zameen blocks the scraper** since ~00:00 (checks #67–#70 all 0 pages): every page answers
503 with a "Security check | Zameen" JavaScript challenge (`.humbucker` bot protection); the site opens fine
in a normal browser. Likely triggered by ~4,300 pages on 10 Nov. **Decided: don't get around it** (no
browser automation, no switching Wi-Fi/IP; user agreed). Paused: `NEW_CHECKS_ENABLED=false` + `schedule pause`.
Plan: wait 2–3 days, then ONE test check from the normal connection (ask the user first); if still blocked,
ask Zameen for official access. Same day: Stop now also cuts through retry waits (`Fetcher.should_stop`,
checked every 3 s; NewAdsCheck sets it), so Stop takes seconds, not up to 6 min. 44 tests pass.
A one-page access test (`check_access.py`, since deleted at the user's request) gave on 2026-11-11: home PC BLOCKED;
**Google Cloud Shell BLOCKED too** (503 "Security check") → bot protection is site-wide (home + cloud), not
just our IP. Don't buy a server for scraping until a check says OK; best route: official access from Zameen.
✅ **Cause found 2026-11-11 18:42**: the block was on the *disguise*, not the IP. Postman (UA "PostmanRuntime")
got the page from the same PC; one test with UA `ZameenScrapper/1.0` → 200, 25 ads parsed. Our old UA claimed
"Chrome/120" without acting like Chrome → 503 challenge. **config.HEADERS now uses the honest name**
Scraping still paused (env false + schedule paused); resume gently, no 4,000-page days.

We are continuing this project **from where it stands now** (see "Current state" below).
Don't rebuild or restructure it. Pick up at "Next step". The README has full details of how it works.

## What this project is

Scrapes **Zameen.com property ads city by city, category by category** (multi-city since 2026-10-28;
36 cities, all fully scraped by 2026-11-01; new-ad checks cover all, one switch `NEW_CHECKS_ENABLED` in .env), into PostgreSQL
(`zameen_islamabad`), storing all fields **except pictures and videos** (counts removed too, user's choice
2026-10-26). It's the
data-collection part of a larger property intelligence system. A **simple read-only viewer** (Flask, `web/`)
was added on 2026-10-26 at the user's request: `python main.py web` → http://127.0.0.1:5000 (local only,
because of the dealer contact data).

It was split out of an earlier exploratory project, `D:\Projects\zameen-scraper` (database `zameen`, with a
Flask search UI and city-level scraping). That project stays separate.

## Decisions already made (don't re-ask)

- Scrape **by category** (property type × for-sale/for-rent), not by whole city.
- Store **every field except pictures and videos** (no photo/video counts either), including dealer contact data (name, mobile, phone, WhatsApp).
  The user chose this knowingly; it's personal data, so keep the DB private.
- Start with **search-page data**. Full description and amenities (from each ad's own page) come later.
  Its columns (`description`, `features`, `details_fetched_on`) were dropped 2026-11-10; add them back then.
- **10 empty / always-the-same columns dropped 2026-11-10 (user's choice)**: listings.rent_frequency,
  permit_number, reference_number (Zameen sends blank), has_tour (always false), zameen_state (always active),
  description, features, details_fetched_on; locations.lat, lng. Values still in `raw`.
- Raw page copies: **only pages that couldn't be used** (`RAW_COPIES=failed`, files `FAILED-*`), kept
  **2 days** (`RAW_KEEP_DAYS=2`). Changed 2026-10-28 at the user's request (was: every page, 7 days).
- Listings are marked inactive only after a complete pass (no failed areas, ≥ 90% coverage).

## Current state (2026-10-26)

- ✅ Project created, `.venv` (Python 3.13) set up, requirements installed.
- ✅ Database `zameen_islamabad` created; all tables from `db/schema.sql` exist (empty).
- ✅ Code complete: `scraper/` (categories, crawler with area splitting + resume + safety checks, fetcher,
  parser, store) and `main.py` (init-db, categories, probe, run, status, report, parse-file).
- ✅ `pytest -q`: 12 tests pass, all offline (fake pages built from `tests/fixtures/search_i11_rent.html`;
  DB tests use the temporary schema `test_isb`).
- ✅ **Probe run 2026-10-26** (38 pages): 32 OK, about 62,800 ads. Both address forms are confirmed.
  Biggest: Residential Plots for sale 29,965 · Houses for sale 10,948 · Flats for sale 4,130 · Houses for rent 3,969.
- ✅ Fixes from the probe: Shops → `Retail_Shops`, Other → `Other_Commercial_Properties` (re-probed OK:
  Shops 1,371 sale / 484 rent, Other 23 / 34). All 36 categories are now confirmed. Residential Plots and Plot Forms have no
  for-rent page (404), so they're set to `"purposes": ["for-sale"]`. 36 categories now.
- ✅ **First category scraped 2026-10-26: `for-rent/Houses_Property`**: 3,971 of 3,971 (100%), 212 pages,
  79 areas, 0 errors, ~14 min. Spot-check of 10 random ads against their zameen.com pages: all fields match.
  5 ads have dealer-typo sizes (0.04 to 45,000 marla); stored as Zameen shows them, filter out in analysis.
- ✅ **Batch 1 done 2026-10-26** (34 categories, 1,651 pages, ~70 min, 0 errors): all ~100% except
  Houses for sale at 90%. Cause: ads on an area itself, not in any listed sub-area (DHA Phase 2: 932,
  B-17: 242). Crawler fixed: if sub-area counts fall short of an area's total by >25, it also pages the
  area itself (test added, 13 pass). Houses for sale re-run with the fix: 10,832 of 10,948 (99%), +1,060 ads
  (1 area 404'd: Sangjani; so nothing marked inactive this pass).
  DB now: 33,646 listings in 35 categories, 1,608 agencies.
- ✅ **Batch 2 done 2026-10-26** (Residential Plots for sale): 29,552 of 30,014 (98%), 1,575 pages, ~2 h,
  0 errors. 7 areas over 1,000 ads (B-17, Faisal Hills, DHA Ph 3 & 5, Gulberg Residencia, ...) are capped.
- ✅ **All 36 categories scraped.** DB: 64,244 listings (49,488 sale / 14,756 rent) vs ~64,700 on Zameen,
  1,755 agencies, 1,047 areas, 213 MB.
- ✅ Viewer redesigned: live "Scraping now" panel (auto-refresh), categories by group with coverage colours,
  sale/rent + verified filters.
- ✅ **New-ad check built 2026-10-27 (Option 2)**: `main.py new` (newest first, reads back to the category's
  previous check minus 1 h, never marks inactive, keeps `last_pass_id`), `scraper/newads.py`, tables
  `new_checks` + `settings`. Schedule: `main.py schedule install|remove|pause|resume|status` (Windows task
  every 6 h, pythonw, log `logs/new-checks.log`); Pause/Resume button in the viewer stops a running scheduled
  check after its current page. One-scrape-at-a-time lock (PostgreSQL advisory lock) on run/probe/new.
  Master switch `NEW_CHECKS_ENABLED=true/false` in `.env` (read fresh each time; currently **false**).
  17 tests pass. **Sort test 2026-10-27 (4 pages): `?sort=date_desc` confirmed, strictly newest first by
  createdAt.** Found: a dealer "refresh" gives an old ad a new createdAt and puts it back on top (page 1 of
  houses: 25/25 already known), so the stop rule is by time, not "no new ads". **Schedule NOT installed.**
  The user asked NOT to build the weekly full scrape for now.
- ✅ **Production review 2026-10-27**: indexes on listings (last_pass_id, first_check_id, zameen_created);
  viewer uses one DB connection per request, caches the Windows task status 60 s, rejects cross-site POSTs
  (Origin/Referer must match host); a 404 on page 1 of a sub-area = "no ads there" (done, not failed, so it
  no longer blocks marking inactive); fetcher retries 500/502/504 too; logs/new-checks.log rotates (2 MB × 5);
  requirements pinned. Overview 1,015 → ~210 ms. 19 tests pass. Not in git yet (asked the user).
- ✅ **Multi-city support built 2026-10-28** (offline only, no city scraped): `categories.json` has
  `default_city` + `cities` (name, slug, `enabled`, `skip`); only Islamabad `enabled: true`.
  `city` column on listings / crawl_passes / category_probes (old rows backfilled 'Islamabad', default dropped);
  new_checks.cities; check markers `new_check:<city>:<category>` (old ones renamed). `--city` on categories /
  probe / run / new / status / report, new `cities` command. `new --all` = every enabled city; a category never
  fully scraped in a city is **skipped** (no starting time). mark_inactive is per city. NewAdsCheck takes
  jobs `[(city, cats)]`. Viewer: 📍 city switch on every page (`?city=`), Cities table on Overview.
  23 tests pass (incl. two-city isolation, never-scraped skip). User plan: switch cities on later, in
  deployment, one by one after each first full scrape (probe → skip list → `run --city X --all` → enabled).
  Zameen city codes
  (from the homepage data, Gwadar from the user): Lahore-1, Karachi-2, Islamabad-3, Rawalpindi-41,
  Multan-15, Gujranwala-327, Faisalabad-16, Peshawar-17, Sialkot-480, Bahawalpur-23, Wah-459,
  Hyderabad-30, Jhelum-19, Abbottabad-385, Murree-36, Gujrat-20, Gwadar-389 (Gwadar not loaded yet).
  Ad counts 2026-10-27 (Homes/Plots/Commercial page 1 totals): Lahore 85,574 · Karachi 53,437 ·
  Rawalpindi 11,011 · Multan 6,762 · Gujranwala 4,713 · Faisalabad 3,824 · Peshawar 2,895 · Sialkot 972 ·
  Bahawalpur 891 · Wah 830 · Hyderabad 319 · Jhelum 308 · Abbottabad 187 · Murree 185 · Gujrat 148.
- ✅ **Time zones fixed 2026-10-28**: all 8 date-time columns are TIMESTAMPTZ (stored UTC; old naive values
  were Pakistan time, converted once by a guarded DO block in schema.sql). Python uses `config.now()` (UTC,
  aware) and `config.today()` (Pakistan date, for first_seen / last_seen / raw-copy folders); `parser._ts`
  returns UTC-aware; `store.connect()` sets the session to Asia/Karachi so commands and the viewer show
  Pakistan time on any server; old zone-less check markers are read as Pakistan time. 25 tests pass
  (new-ad check tested with the DB session in Asia/Karachi and in UTC).
- ✅ **New-ad check reads group lists 2026-10-28**: per city 6 lists (`categories.Group`: Homes / Plots /
  Commercial × sale / rent, URL `/{Rentals_}Homes/<city>-<n>.html?sort=date_desc`) instead of 36. Test of
  Islamabad's 6 lists: strictly newest first, types mixed, all types ours. Each ad saved under its own key
  (`purpose/type_slug`); types not chosen are passed over. Markers stay per category; a group reads back to
  the earliest of its categories; a group with a never-scraped category is skipped. Results are per group
  (`by_category` new counts). 26 tests pass. Not yet run for real.
- ✅ **2026-10-28 runs**: new-ad check #4 on the 6 group lists (first real run): 1,028 new, 238 pages,
  0 errors; every ad filed under its own type, none older than the previous check. Re-read of Islamabad
  Houses for sale + Residential Plots for sale started (pass #38) and **stopped by the user after 28 pages**
  (resumable; 131 areas pending). Rate test (page 1 of 6 newest-first lists × 17 cities, 91 pages, nothing
  saved): ~3,900 brand-new and ~27,000 new+refreshed ads/day for all 17 cities (Lahore ~1,365 / ~12,700);
  Zameen ad IDs rise ~4,900/day for all Pakistan. User wants checks to stay under 1,000 ads; discussed
  per-city / per-list intervals (not built).
- ✅ **Rawalpindi first full scrape 2026-10-28**: 11,046 ads (Zameen ~11,025), all 32 categories 100%,
  613 pages. A connection drop (17:08–17:14) paused Houses for sale and failed Houses for rent; re-run
  finished both. Still `enabled: false` (user decides).
- ✅ **13 smaller cities scraped 2026-10-28** (17:59–19:39, 1,327 pages, 0 network errors), all ~100%:
  Multan 6,714 · Gujranwala 4,704 · Faisalabad 3,857 · Peshawar 2,910 · Sialkot 977 · Bahawalpur 898 ·
  Wah 835 · Gwadar 414 · Hyderabad 320 · Jhelum 311 · Murree 189 · Abbottabad 188 · Gujrat 149.
  DB ≈ 99,600 ads in 15 cities. All still `enabled: false`. **Lahore + Karachi: user wants them tomorrow
  (2026-10-29), ask first** (~6,000 pages, ~7 h).
- ✅ **404 category = "no ads", not failed (2026-10-28, user chose this over skip lists)**: a category with no
  page in a city (e.g. no flats in Gujrat) finishes "done, 0 ads"; read again every pass so its first ad is
  found; a 404 alone never marks ads inactive. Skip lists stay empty. Today's 178 such passes relabelled in
  the DB. 29 tests pass.
- Zameen has **228 cities** (list in the test fixture's window.state). **2026-10-29**: counted 23 the user
  picked (91 pages): 19 have ads and were **added to categories.json (36 cities now, off, not scraped)**:
  Sheikhupura 166 · Sargodha 99 · Quetta 97 · Rahim Yar Khan 93 · Sahiwal 85 · Mardan 72 · Kasur 57 ·
  Chakwal 50 · Haripur 48 · Attock 41 · Taxila 26 · Mirpur 21 · Gujar Khan 20 · Okara 15 · Fateh Jang 9 ·
  Mansehra 4 · Sukkur 3 · Larkana 3 · D.I. Khan 2 (≈ 930 ads in all). 0 ads, not added: Dera Ghazi Khan,
  Jhang, Muzaffarabad, Nawabshah. **All 19 scraped 2026-10-29** (11:51–12:36, ~700 requests of which ~350
  were empty-category 404s, 0 errors): every city 100% of its count, 911 ads. All still off.
  DB ≈ 100,500 ads in 34 cities. Left: **Lahore + Karachi** (ask first, ~6,000 pages, ~7 h).
- ⏸ **Karachi full scrape PAUSED 2026-10-29 15:01 by the user (until after their exam)**: 11 categories done
  (all ~100%, ~34,000 ads, 0 errors); Residential Plots for sale stopped at 7,696 of 11,358 (pass #1242, 50 of
  156 areas left, status 'running' = resumable). Note: `run --all` would RE-READ done categories (a done
  pass isn't resumed, a new one starts), so resume with only what's left. **Resumed 2026-10-31 00:33** with
  the user's yes: `run --city Karachi --group Plots --group Commercial` (log appended to full-Karachi.log).
  ✅ **Finished 01:16**: 634 pages, 0 errors, all 22 categories 100% (Residential Plots 11,362 of 11,358).
  **Karachi complete: 53,476 ads, all 36 categories 100% (Plot Files for rent: no page = 0 ads).** Still `enabled: false`. DB ≈ 154,000 ads in
  35 cities. Lahore not started (ask first, ~85,800 ads, ~3,600 pages, ~4 h).
- ✅/⚠ **Lahore full scrape 2026-10-31 15:26–21:30** (`run --city Lahore --all`, 3,834 pages): 35 of 36
  categories 100% (Houses sale 24,751, rent 6,730; Plot Files rent = no page). **Residential Plots for sale
  only 17,086 of 31,775 (54%)**, pass #1278: sub-areas summed 42 short of the city total (> NOT_IN_SUB_AREAS_MAX
  25), so the whole city list (1,271 pages) was read as one area; page 697 gave HTTP 500 three times → area
  failed, category ended. Lahore in DB: 71,250 ads; DB 225,211 ads. Resumed from page 697 (user's yes,
  22:15): 500 again ×3 → **Zameen stops serving deep pages (~past 696)**. Fix (offline): crawler
  `READ_WHOLE_MAX_PAGES = 400`: a list longer than that is split into areas even if a few ads sit outside them
  (those are missed and logged). 30 tests pass. Next: re-read Lahore Residential Plots for sale
  (`run --city Lahore --category for-sale/Residential_Plots`, new pass, ~1,400 pages, ~1.5–2 h): ask first.
  ✅ **Done 2026-11-01 01:17** (pass #1300, user's yes): split into 269 areas (38 outside ads missed),
  1,447 pages, 0 errors, **31,817 of 31,870 (100%)**, +14,766 new, 35 marked inactive.
  **Lahore complete: 86,016 ads, all 36 categories ~100%.** Still `enabled: false`.
  **All 36 cities now fully scraped: DB 239,977 ads.**
- ✅ **Islamabad re-read 2026-11-01 01:30–03:31** (user's yes; log logs/full-Islamabad-reread.log, 1,813 pages,
  0 errors): Houses for sale (pass #38 resumed) 10,801 of 10,874 (99%), +567 new, 852 marked inactive;
  Residential Plots for sale 30,062 of 30,077 (100%), +2,308 new, 2,742 marked inactive. The old 1,000-cap
  gaps are closed. Islamabad: 65,391 active / 68,935 all. Other 34 Islamabad categories last read 2026-10-26.
- ✅ **One switch for new-ad checks 2026-11-02 (user's request)**: per-city `"enabled"` REMOVED from
  categories.json and the code. Every city in categories.json is in `new --all` / the schedule / the viewer
  button; the only on/off is `NEW_CHECKS_ENABLED` in .env (still **false**). `cities` command shows that switch;
  viewer Cities table lost its on/off column. README updated. 30 tests pass. Note: the first check after
  switching on reads back to each city's last full scrape (days for most cities): many pages, ask first.
- ✅ **Per-city pace for new-ad checks 2026-11-02 (user chose option 2, automatic)**: `scraper/pace.py`.
  The task still wakes every NEW_CHECK_EVERY_HOURS (6); scheduled runs check only DUE cities. After each
  clean city check: interval = time for ~25 brand-new ads (TARGET_NEW) at that city's rate, speed-up at once,
  slow-down max ×2 per check, whole 6 h steps, between 6 h and `NEW_CHECK_MAX_HOURS` (default **24**; user may
  raise to 48/72 after 2–3 weeks). Due = next check before now + half a step. Failed / capped / stopped city:
  pace unchanged, stays due. State in settings `pace:<city>` (JSON every/last/next/new/hours). A group list
  that 404s = no ads (not an error). Hand-started checks check every city and also set the pace.
  `cities` command + viewer Cities table show interval and next check; `schedule status` shows due/waiting.
  41 tests pass (rules, fake-clock month, burst, failure, stop, 404, scheduled due-only run). Not run for real.
- ✅/⚠ **Islamabad catch-up check #5 2026-11-02 12:50–13:56** (user's yes, log logs/catchup-Islamabad.log):
  2,282 new ads, 880 pages (estimate was ~1,100). Plots for sale FAILED at page 401 (HTTP 500 ×3):
  **Zameen serves newest-first lists only to page 400 (10,000 ads)**. Read back to 28 Oct 19:42 of the
  needed 28 Oct 14:26. Fixes (offline, 43 tests pass): newads `SORTED_MAX_PAGES = 400` (stop there =
  "capped", markers set, noted; not a failure); `store.last_checked` = LATER of the check marker and the
  last full pass start (it used the marker first, so Islamabad plots read back to 28 Oct despite the
  1 Nov full pass). Islamabad for-sale plot markers set by hand to 13:17:11 PKT (when that list was read);
  the 5 h gap on 28 Oct (commercial plots / files) is left for the next full scrape. Islamabad pace not set
  yet (failed run): due at the next check.
- ✅ **Catch-up checks 2026-11-02** (user's yes): Karachi #6 15:29–16:13, 2,385 new, 639 pages, 0 errors →
  pace 6 h; Rawalpindi #7 16:13–16:23, 730 new in 119 h, 152 pages, 0 errors → pace 6 h (more new ads than
  the rate test suggested). Lahore #8 16:26–17:13: 2,119 new in 46.5 h, 696 pages, 0 errors → pace 6 h.
  Then (user's yes, 17:13–17:38, logs/catchup-<City>-2.log, 0 errors): Islamabad #9 316 new, 95 pages → 6 h;
  Multan #10 422 new, 78 pages → 6 h; Faisalabad #11 259 new, 48 pages → 6 h; Gujranwala #12 288 new,
  70 pages → 6 h; Peshawar #13 208 new, 28 pages → 12 h. Left without a catch-up (user's choice): the 28 small
  cities (tiny; the first scheduled run catches them up). NEW_CHECKS_ENABLED still false.
- ✅/⚠ **Big-city update 2026-11-04 12:24–14:22** (user's yes, logs/update-<City>-1104.log), ~44 h since
  2 Nov: Lahore #14 3,098 new, 694 pages · Islamabad #15 1,759, 395 · Karachi #16 1,645, 337 ·
  Rawalpindi #17 290, 65 · Multan #18 349, 43 · Faisalabad #19 98, 17 (all 0 errors, pace 6 h).
  Gujranwala #20 177 new, 44 pages, but Commercial for rent FAILED; Peshawar #21 154 new, 11 pages, 5 of 6
  groups FAILED. Cause: network ConnectionError on the PC side (14:16–14:22), not a block. Failed groups keep
  their old markers and pace (stay due): the next check of those two cities reads back to 2 Nov, nothing lost.
- ✅ **Pre-handover update 2026-11-05 12:04–13:07** (user's request, logs/update-<City>-1105.log), 0 errors:
  Lahore #22 1,633 new, 414 pages · Islamabad #23 942, 223 · Karachi #24 733, 168 · Rawalpindi #25 131, 30 ·
  Multan #26 136, 24 · Faisalabad #27 98, 18 · Gujranwala #28 91, 23 · Peshawar #29 157, 24 (caught up
  since 2 Nov). Total 3,921 new, 924 pages; all 8 paced 6 h. DB 263,350 ads (260,037 active).
  Next: handover to DevOps (stop PC scraping, pg_dump per docs/DATABASE_TRANSFER.md).
- ⏹ **All-city update 2026-11-10 12:47 STOPPED by the user at 13:41** (check #30, Lahore only, 790 pages,
  3,283 new): Homes sale capped at 400 pages, Homes rent done, Plots sale partly read (marker not moved:
  re-read next time). Then the 10 columns were dropped (init-db), 43 tests pass, viewer pages OK.
  DB 266,633 ads. The other 35 cities still need their update (Lahore Homes sale has a gap from the cap).
- ▶ **2026-11-10 ~13:50: scraping kept running on the PC until deployment (user's request)**:
  `NEW_CHECKS_ENABLED=true` in .env (Windows task every 6 h, 00/06/12/18, only while logged on and on
  power), and a hand-started catch-up of all 36 cities (logs/catchup-<City>-1110.log). Before the dump:
  switch to false + `schedule remove`.
  ✅ Catch-up done 13:50–17:10: checks #31–#66, 10,717 new ads, 2,847 pages, 0 errors. 28 small cities → 12 h,
  big six → 6 h; Lahore + Islamabad capped (pace kept 6 h). Gaps left by the 400-page cap (close with a full
  re-read, ask first / or on the server): Lahore Plots sale 5 Nov 12:20–7 Nov 17:47 (~2,000 ads), Lahore Homes
  sale 5 Nov 12:04–6 Nov 10:09 (~350), Islamabad Plots sale 5 Nov 12:39–21:08 (~180). DB 277,350 ads (274,064 active).
  ✅ **Islamabad Plots sale gap closed 2026-11-10 17:14–18:53** (user's yes, passes #1302–#1307, all 6 for-sale
  plot categories, 1,479 pages, 0 errors, all 100%): +277 new, 3,918 marked inactive (Residential 3,562).
  Left: the two Lahore gaps.
- **The first commit (5c9cb9d) was pushed to the PUBLIC repo** by the user; it includes
  tests/fixtures/search_i11_rent.html with real dealer names and phones (user informed).
- Production improvement list (explained 2026-10-28, user picks one at a time): ~~time zones~~, ~~group lists~~, weekly
  full-scrape schedule, backups, alerts, git push (repo is PUBLIC: test fixture has real dealer phones,
  user to choose anonymize / make private), new-ad check on 6 group lists per city instead of 36,
  summary table for the viewer, disk trimming (~~raw copies: failed only, 2 days~~; `raw` column trim
  not done), versioned migrations, server setup, duplicates/typo flags.
- Possible next: get past the 1,000 cap in capped areas (re-read them sorted by price low→high and high→low;
  needs a few test page loads to confirm Zameen's sort URL), ad detail pages, nightly schedule.

## Next step

1. ~~Probe all categories~~, ~~fix PROBLEM categories~~, ~~re-probe the fixed codes~~: done.
2. ~~One category end to end + spot-check~~: done (Houses for rent). Next: agree the order for the other 35
   with the user (ask permission per run). Suggested: everything except Residential Plots for sale first,
   then Residential Plots for sale (~30k ads, ~1,300+ pages, ~1.5 h) on its own.
3. New-ad check: (a) ~~sort test~~ done; (b) ~~first real `new --all`~~ done 2026-10-27 11:50 (check #1:
   580 new ads, 188 pages, ~12 min, 0 errors; Residential Plots capped at 40 pages as expected after a
   day's backlog; all new ads posted after the last full pass, complete data). Viewer page `/new` added
   (`listings.first_check_id`). Spot-check of 10 random new ads vs zameen.com: all fields match.
   The 117 price changes of check #1 were all on old (yesterday's) ads that dealers refreshed.
   (c) ✅ Windows task installed 2026-10-27 (renamed 2026-11-11: "Zameen Scrapper new ads"), every 6 h from 00:00, pythonw,
   runs only while the user is logged on, not on battery). **Kept OFF at the user's request:
   `NEW_CHECKS_ENABLED=false`** (test-started once: logged "skipped", loaded nothing). The user turns it on
   by setting `true` in .env.
   (d) ✅ "▶ Check for new ads now" button on `/new` (starts `main.py new --all --background`, detached,
   logs to logs/new-checks.log; disabled while any scrape holds the lock) and "■ Stop" (settings.stop_check,
   stops any check after its current page). The button ignores NEW_CHECKS_ENABLED (that's for the schedule).
   Pressing it is the user's own permission for that run. 18 tests pass.
4. Later: weekly full scrape (user said not now), ad detail pages (description/amenities).

## Technical notes

- Windows; PowerShell is the main shell (Bash is also available). Use `.venv\Scripts\python`.
- Zameen pages embed data as JSON in `window.state`. The parser reads `algolia.content.hits`, the totals
  (`nbHits`, `nbPages`), the active filters (`purpose`, `category.slug`, `location.slug`) to check each page
  is what was asked for, and `searchPageInternalLinks.data.locations` for sub-areas.
- ~~Zameen pages through only the first 1,000 results~~ **WRONG (tested 2026-10-27)**: pages 41, 100 and 400 of
  Houses for sale (437 pages) all returned 25 real, distinct ads. There is no 1,000 limit. **Code updated
  2026-10-27**: `SPLIT_ABOVE_PAGES=40` (split long lists into areas, for steadiness, not because of a limit);
  an area with >25 ads outside its sub-areas is read whole (all pages) instead of split; nothing is capped;
  `MAX_PAGES=2000` is only a safety stop; new-ad checks read back as far as needed; interval is
  `NEW_CHECK_EVERY_HOURS` in .env (default 6, any value is safe). 20 tests pass.
  The ~1% previously "capped" (B-17, DHA Ph 2/3/5, Faisal Hills, Gulberg Residencia, ...) needs a re-run of
  Houses for sale and Residential Plots for sale to recover (ask the user).
- Thumbnail URL pattern (not used here): `https://media.zameen.com/thumbnails/<photo id>-400x300.webp`.
- Agency profile URLs couldn't be built from the data (every guess returned 404).
- Space measured on the old project: about 3.3 KB per listing in the DB (80% is the `raw` JSON),
  about 200 KB per raw page copy.
