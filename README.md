# personal-vacancy-finder

Finds job vacancies across many sources and collects them into one Postgres database, so discovery is thorough rather than limited to the few boards checked by hand.

Two containers run under Docker Compose:

- **db**: Postgres 18, holding sources, companies, jobs and postings. Data lives in the `pgdata18` volume.
- **worker**: a Python process that applies database migrations on start, then runs every registered collector on a fixed interval.

## Running it

```sh
cp .env.example .env      # then set POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f worker
```

The database is published on `127.0.0.1:5432` (change with `POSTGRES_PORT`), so any desktop SQL client can connect using the credentials in `.env`.

## Results page

The worker scores every open job against the search criteria after each cycle and serves a results page at http://localhost:8080 (this machine only; change the port with `WEB_PORT` in `.env`). Jobs are ranked by score, with the reasons shown under each one. Tabs split them into matches, maybes and filtered-out jobs, and buttons mark a job interested, not interested or applied; marked jobs move to their own tabs and stay marked across rescoring.

Filters above the list narrow every tab, and the tab counts, to jobs first seen in the last 6 hours, 24 hours, 3, 7 or 30 days, or since a date and time you pick, and to a minimum score. Each job shows the date and time it was first seen, in Warsaw time (change with `DISPLAY_TZ`). Sort by best score (the default) or newest first. The filters are part of the page address, so a bookmark such as http://localhost:8080/?seen=24&sort=new opens the last day's finds.

**Closing dates.** Where a job's closing date is known it shows after "first seen": "apply by 15 Nov" for a deadline the employer states in the advert text (English wording such as "Application deadline:", "Apply by", "Closing date:"), or "ad ends 26 Nov" when the board itself gives an expiry date (Just Join IT, about 30 days after posting unless renewed; Himalayas; Traffit). Dates under two days away are shown in red. Greenhouse, Lever, Ashby, Workable, Personio, Teamtailor, Recruitee, Remote OK, We Work Remotely, Arbeitnow and Hacker News give no closing date, so for those only a deadline written in the advert is found. The **Closing** filter picks jobs closing within 2 or 7 days, or any job with a stated deadline still to come, and the **Closing soonest** sort puts the nearest first, e.g. http://localhost:8080/?closes=2&sort=closing.

Under each job, **Feedback** opens three notes, "Reject for:", "Rate higher for:" and "Rate lower for:", plus "Score higher" and "Score lower" buttons that record which way the scoring weights should have moved for that job. Each note is saved in the `job_feedback` table with a copy of the ad text, all its postings and the score and reasons at the time, so it still makes sense after the job changes or is rescored. A rejection also marks the job not interested. Saved notes show under the job and can be removed with ×. Nothing reads them automatically yet: they are there for tuning the filters and weights later.

The rules live in `worker/src/vacancy_finder/matching.py`, written to be read and tuned: role level, Python and SQL, location and work mode, sector, contract and pay add or take away points, and a few things rule a job out entirely (an advert not in English, a requirement to speak Polish or German, a non-engineering role, on-site outside Warsaw, remote limited to other continents, tobacco or gambling).

## How data is stored

- A **posting** is one advert on one source, unique by `(source, external_id)`.
- A **job** is the real vacancy behind one or more postings. Postings with the same normalized company name and title share a job, so a role advertised on three boards is counted once.
- A posting that disappears from its source after a successful run gets `closed_at` set. A job closes when none of its postings is open.
- Every collector run is logged in `collection_runs`, with counts and any error.

Schema changes go in `worker/migrations/` as new numbered `.sql` files. The worker applies each one once.

## Development

```sh
cd worker
uv sync                                                # creates .venv with the dev tools
uv run pytest                                          # unit tests only
TEST_DATABASE_URL=postgresql://... uv run pytest       # plus database tests
```

Dependencies are pinned in `worker/uv.lock`, which the Docker build also installs from. After changing them in `pyproject.toml`, run `uv lock` and commit the lockfile.

The database tests wipe the schema of the database they point at, so give them a disposable one, e.g. `docker compose exec db createdb -U vacancies vacancies_test`.

## Sources

See [docs/job-sources.md](docs/job-sources.md) for the source map and build order. Collectors live in `worker/src/vacancy_finder/collectors/` and are registered in `COLLECTORS`.

### Company careers feeds

The careers-feed collectors poll every company in `worker/seeds/companies.csv` that has an `ats_kind` and `ats_slug`. The file is mounted into the worker, so edits apply on the next cycle without a rebuild. Columns: `name`, `sectors` (separated by `;`), `ats_kind`, `ats_slug`, `website`, `notes`, `careers_url`, `link_pattern`.

| `ats_kind` | `ats_slug` |
|---|---|
| `greenhouse`, `lever`, `lever-eu`, `ashby`, `workable`, `recruitee`, `traffit` | the company's slug on that system |
| `personio` | the board's host, e.g. `liveeo-gmbh.jobs.personio.de` |
| `teamtailor` | the careers site, e.g. `jobs.tibber.com` |
| `page` | a plain careers page URL; `link_pattern` is a regex matching its job links' `href`s |

To check new rows against the live feeds without a database: `python -m vacancy_finder.check_companies "KP Labs"` (or `--kind page`).

To find a company's feed, run the probe, which tries likely slugs on each ATS and prints ready-to-paste rows:

```sh
cd worker
python -m vacancy_finder.probe "KP Labs" "ICEYE"
python -m vacancy_finder.probe --file seeds/candidates.txt
```

Check each hit's job count looks right before adding it, since a common name can match a different company. The probe waits `--delay` seconds (default 1) between companies. `seeds/candidates-round2.txt` and `candidates-round3.txt` hold later rounds of names from sector lists. Probe several companies at once with `--workers 4`. Teamtailor sites on a `teamtailor.com` subdomain are probed too.

The adverts already collected from boards (Hacker News, Himalayas, Arbeitnow and the rest) often link to the employer's own ATS board. To list the boards they link to that aren't in the company list yet, with the best-scoring ones first, run against the database:

```sh
DATABASE_URL=postgresql://vacancies:...@127.0.0.1:5432/vacancies python -m vacancy_finder.discover > found.csv
```

If one company's feed fails, its postings are left open rather than closed, and the run carries on with the rest.

### Just Join IT

justjoin.it's robots.txt disallows its API, so the collector reads only what the site offers crawlers: the sitemap of active adverts and each advert's page. To stay polite it opens only adverts whose URL contains a role keyword as a whole word (`JUSTJOINIT_KEYWORDS`), only re-opens an advert when its sitemap date changes, waits `JUSTJOINIT_DELAY_SECONDS` between pages, and opens at most `JUSTJOINIT_MAX_PAGES` per run. The first runs work through the backlog; after that each run fetches only new or changed adverts.

Every posting gets a detected `language` ('en', 'pl' or 'de'). Adverts in Polish or German are kept, so they aren't re-fetched, and are left out at the matching stage.

To check the parser against the live site: `python -m vacancy_finder.check_justjoinit 5`.

No Fluff Jobs disallows automated access to both its API and its advert pages, so it is covered through its email alerts instead (see below).

### Remote boards

Himalayas (searched with role queries, limited to jobs open to people in Poland), Remote OK, We Work Remotely (programming and DevOps feeds), Arbeitnow (remote jobs and jobs in Poland only) and the Hacker News "Who is hiring?" threads (this month and last, re-read every run so new comments are picked up). These feeds show only recent jobs, so a posting is closed once it has been missing for a couple of weeks (45 days for Hacker News) rather than on its first absence. Remotive is left out: its robots.txt disallows the API.

Settings: `HIMALAYAS_QUERIES` (comma-separated), `HIMALAYAS_MAX_PAGES` (20 jobs per page, per query), `ARBEITNOW_MAX_PAGES`.

To check them against the live sites without touching the database: `python -m vacancy_finder.check_feeds` (or name sources, e.g. `check_feeds himalayas`).

### Job-alert emails

LinkedIn, Otta (Welcome to the Jungle) and No Fluff Jobs are covered through their job-alert emails. Send the alerts to a dedicated inbox and put its login in `.env` as `IMAP_USER` and `IMAP_PASSWORD` (see `.env.example`; the default host is Interia's `poczta.interia.pl`). The worker reads the inbox read-only, so nothing is marked read or moved, and keeps every alert in the `email_messages` table. Each run re-reads alerts from the last `EMAIL_ALERT_DAYS` (30), so parser fixes apply to old alerts, and a job closes once its newest alert is older than that.

To see what the parsers make of the inbox without touching the database: `set -a; . ../.env; set +a; python -m vacancy_finder.check_email` from the `worker` folder. Add `--save some/folder` to keep the messages as `.eml` files.
