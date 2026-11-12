# Zameen database transfer to server

Copy the PostgreSQL 18 database `zameen_islamabad` (818 MB, about 240,000 property listings) from the Windows
PC to the server with one `pg_dump` file and `pg_restore`. Expect 20–40 minutes in total, most of it the file copy.

## What you're moving

One PostgreSQL database. It has no extensions beyond the built-in `plpgsql`, so a plain dump and restore is all
it needs.

| Item | Value |
| --- | --- |
| Database | `zameen_islamabad` |
| PostgreSQL version on the PC | 18.3 |
| Size on disk | 818 MB |
| Tables (schema `public`) | listings, agencies, locations, price_history, listing_changes, crawl_passes, crawl_tasks, category_probes, new_checks, settings |
| Largest table | `listings`, about 240,000 rows |
| pg_dump on the PC | `C:\Program Files\PostgreSQL\18\bin\` |

The `settings` table holds the scraper's state (when each city was last checked). It must come across with
everything else, or the first new-ad check on the server re-reads days of ads.

## Before you start

- **Server PostgreSQL must be 18 or newer.** `pg_restore` can't read a dump made by a newer version than itself.
  On Ubuntu/Debian, install it from the PostgreSQL apt repository (apt.postgresql.org), since the distro's own
  package is often older.
- **Free disk on the server:** at least 3 GB (dump file + restored database + room to grow about 15 MB a day).
- **Python 3.13** for the app itself (separate from the database move).
- **Stop scraping on the PC before the dump, for good.** Anything the PC scrapes after the dump is not in the copy.
  The project owner runs these on the PC:

  ```
  .venv\Scripts\python main.py status            # nothing should show 'running'
  .venv\Scripts\python main.py schedule remove   # removes the PC's every-6-hours task
  ```

  From then on the server is the only place that scrapes. Two machines scraping into two databases would split
  the data.

## Step 1, on the Windows PC: make the dump

In PowerShell. It asks for the `postgres` user's password (the one in the project's `.env`). Takes a few minutes;
the file comes out a few hundred MB because `-Fc` compresses it.

```
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -U postgres -h localhost -d zameen_islamabad -Fc -f D:\zameen_islamabad.dump
```

Check the file is readable (prints the start of its table of contents):

```
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" -l D:\zameen_islamabad.dump | Select-Object -First 15
```

Note the row counts, to compare on the server in Step 4:

```
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -d zameen_islamabad -c "SELECT 'listings' t, count(*) FROM listings UNION ALL SELECT 'agencies', count(*) FROM agencies UNION ALL SELECT 'locations', count(*) FROM locations UNION ALL SELECT 'price_history', count(*) FROM price_history UNION ALL SELECT 'settings', count(*) FROM settings"
```

## Step 2: copy the file to the server

Use `scp` (built into Windows 10/11 PowerShell) or WinSCP. Both are encrypted. Replace `USER` and `SERVER`:

```
scp D:\zameen_islamabad.dump USER@SERVER:/tmp/zameen_islamabad.dump
```

Never send the file by email, chat, or a public download link: it contains dealers' names and phone numbers
(see Security).

## Step 3, on the server: create the database and restore

Commands for Linux (Ubuntu/Debian) with PostgreSQL 18 installed. A dedicated user `zameen` owns the database, so
the app never runs as `postgres`.

1. Create the user (it asks for a new password; keep it for Step 4) and an empty database it owns:

   ```
   sudo -u postgres createuser --pwprompt zameen
   sudo -u postgres createdb -O zameen zameen_islamabad
   ```

2. Restore. `--no-owner --role=zameen` makes `zameen` the owner of every table (on the PC they belonged to
   `postgres`). `-j 4` restores with 4 parallel jobs:

   ```
   sudo -u postgres pg_restore -d zameen_islamabad --no-owner --role=zameen -j 4 /tmp/zameen_islamabad.dump
   ```

3. Refresh the planner statistics (a restore doesn't), so queries are fast from the start:

   ```
   sudo -u postgres vacuumdb --analyze -d zameen_islamabad
   ```

If `pg_restore` reports errors, stop and send its output to the project owner rather than re-running it over a
half-restored database. To start again cleanly: `sudo -u postgres dropdb zameen_islamabad`, then repeat from the
`createdb`.

## Step 4: check the copy, then point the app at it

1. Run the same count query as in Step 1. Every number must match the PC's exactly:

   ```
   sudo -u postgres psql -d zameen_islamabad -c "SELECT 'listings' t, count(*) FROM listings UNION ALL SELECT 'agencies', count(*) FROM agencies UNION ALL SELECT 'locations', count(*) FROM locations UNION ALL SELECT 'price_history', count(*) FROM price_history UNION ALL SELECT 'settings', count(*) FROM settings"
   ```

2. In the project folder on the server, set the connection in `.env` (copy `.env.example` to `.env` first; the
   other values there can stay):

   ```
   DATABASE_URL=postgresql://zameen:<password from Step 3>@localhost:5432/zameen_islamabad
   ```

3. Check the app sees the data. These are offline: they read the database and load nothing from zameen.com.

   ```
   .venv/bin/python main.py init-db      # confirms the tables are up to date; changes nothing in the data
   .venv/bin/python main.py cities       # 36 cities with their ad counts
   .venv/bin/python -m pytest -q         # 44 tests; they use a temporary schema test_isb and drop it after
   ```

The app shows all times in Pakistan time whatever the server's own time zone, so there's nothing to set for that.

Don't start any scraping commands (`run`, `new`, `probe`, `schedule install`) yet. The project owner switches
scraping on after this check. (`schedule install` uses the Windows Task Scheduler; on Linux the schedule will be
a cron job or systemd timer instead.)

## Security

The database holds personal data: dealers' names, mobile and WhatsApp numbers. Treat it like customer data.

- [ ] PostgreSQL listens on localhost only (`listen_addresses = 'localhost'`, the default); port 5432 is not open
      in the firewall.
- [ ] The app connects as `zameen`, never as `postgres`; its password is only in the server's `.env`
      (permissions 600) and never committed to git.
- [ ] After Step 4 passes, delete the dump from both machines: `rm /tmp/zameen_islamabad.dump` on the server,
      `D:\zameen_islamabad.dump` on the PC.
- [ ] The viewer (`python main.py web`) binds to 127.0.0.1 only. If others need it, put it behind a login and
      HTTPS; don't expose it as is.
- [ ] Set up a daily `pg_dump` backup kept off the server, and test one restore.
