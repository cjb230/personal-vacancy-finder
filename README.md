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

## How data is stored

- A **posting** is one advert on one source, unique by `(source, external_id)`.
- A **job** is the real vacancy behind one or more postings. Postings with the same normalized company name and title share a job, so a role advertised on three boards is counted once.
- A posting that disappears from its source after a successful run gets `closed_at` set. A job closes when none of its postings is open.
- Every collector run is logged in `collection_runs`, with counts and any error.

Schema changes go in `worker/migrations/` as new numbered `.sql` files. The worker applies each one once.

## Development

```sh
cd worker
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                          # unit tests only
TEST_DATABASE_URL=postgresql://... pytest       # plus database tests
```

The database tests wipe the schema of the database they point at, so give them a disposable one, e.g. `docker compose exec db createdb -U vacancies vacancies_test`.

## Sources

See [docs/job-sources.md](docs/job-sources.md) for the source map and build order. Collectors live in `worker/src/vacancy_finder/collectors/` and are registered in `COLLECTORS`.

### Company careers feeds

The Greenhouse, Lever (global and EU), Ashby and Workable collectors poll every company in `worker/seeds/companies.csv` that has an `ats_kind` and `ats_slug`. The file is mounted into the worker, so edits apply on the next cycle without a rebuild. Columns: `name`, `sectors` (separated by `;`), `ats_kind`, `ats_slug`, `website`, `notes`.

To find a company's feed, run the probe, which tries likely slugs on each ATS and prints ready-to-paste rows:

```sh
cd worker
python -m vacancy_finder.probe "KP Labs" "ICEYE"
python -m vacancy_finder.probe --file seeds/candidates.txt
```

Check each hit's job count looks right before adding it, since a common name can match a different company.

If one company's feed fails, its postings are left open rather than closed, and the run carries on with the rest.

### Just Join IT

justjoin.it's robots.txt disallows its API, so the collector reads only what the site offers crawlers: the sitemap of active adverts and each advert's page. To stay polite it opens only adverts whose URL contains a role keyword as a whole word (`JUSTJOINIT_KEYWORDS`), only re-opens an advert when its sitemap date changes, waits `JUSTJOINIT_DELAY_SECONDS` between pages, and opens at most `JUSTJOINIT_MAX_PAGES` per run. The first runs work through the backlog; after that each run fetches only new or changed adverts.

Every posting gets a detected `language` ('en', 'pl' or 'de'). Adverts in Polish or German are kept, so they aren't re-fetched, and are left out at the matching stage.

To check the parser against the live site: `python -m vacancy_finder.check_justjoinit 5`.

No Fluff Jobs disallows automated access to both its API and its advert pages, so it will be covered through its email alerts instead.

### Remote boards

Himalayas (searched with role queries, limited to jobs open to people in Poland), Remote OK, We Work Remotely (programming and DevOps feeds), Arbeitnow (remote jobs and jobs in Poland only) and the Hacker News "Who is hiring?" threads (this month and last, re-read every run so new comments are picked up). These feeds show only recent jobs, so a posting is closed once it has been missing for a couple of weeks (45 days for Hacker News) rather than on its first absence. Remotive is left out: its robots.txt disallows the API.

Settings: `HIMALAYAS_QUERIES` (comma-separated), `HIMALAYAS_MAX_PAGES` (20 jobs per page, per query), `ARBEITNOW_MAX_PAGES`.

To check them against the live sites without touching the database: `python -m vacancy_finder.check_feeds` (or name sources, e.g. `check_feeds himalayas`).
