# personal-vacancy-finder

Finds job vacancies across many sources and collects them into one Postgres database, so discovery is thorough rather than limited to the few boards checked by hand.

Two containers run under Docker Compose:

- **db**: Postgres 18, holding sources, companies, jobs and postings. Data lives in the `pgdata18` volume.
- **worker**: a Python process that applies database migrations on start, then runs each collector on its own interval: the job-alert inbox every 15 minutes, job boards every 2 hours and company careers feeds every 6 hours (`EMAIL_INTERVAL_MINUTES`, `BOARD_INTERVAL_MINUTES`, `ATS_INTERVAL_MINUTES` in `.env`). Each source is due once its interval has passed since its last run started, so a restart only runs what is due, and sources run one after another, never at the same time.

## Running it

```sh
cp .env.example .env      # then set POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f worker
```

The database is published on `127.0.0.1:5432` (change with `POSTGRES_PORT`), so any desktop SQL client can connect using the credentials in `.env`.

## Results page

The worker scores every open job against the search criteria after each round of collection and serves a results page at http://localhost:8080 (this machine only; change the port with `WEB_PORT` in `.env`). Jobs are ranked by score, with the reasons shown under each one. Tabs split them into matches, maybes and filtered-out jobs, and buttons mark a job interested, not interested or applied; marked jobs move to their own tabs and stay marked across rescoring. An applied job also gets a **Rejected** button for when the employer turns the application down: it moves to the **Rejected** tab, keeping its applied date and folder and showing when it was marked rejected. Rejected jobs are not re-checked against their sources. A job you haven't judged yet also gets a **Gone** button, for when you open the advert and it has already disappeared. It moves to the **Gone** tab and is kept apart from Not interested, so a classifier trained on these decisions later won't count a job you never saw as one you turned down. Gone is a manual label: when a source stops listing a job, it simply drops off Matches and Maybes, and only interested or applied jobs are re-checked (see "How data is stored"). Gone jobs are not re-checked either; use Clear to undo one.

Filters above the list narrow every tab, and the tab counts, to jobs first seen in the last 6 hours, 24 hours, 3, 7 or 30 days (one click on the **First seen** chips; the chosen period is highlighted and "Any time" turns it off), or since a date and time you pick, and to a minimum score. Each job shows the date and time it was first seen, in Warsaw time (change with `DISPLAY_TZ`). Sort by best score (the default) or newest first. The filters are part of the page address, so a bookmark such as http://localhost:8080/?seen=24&sort=new opens the last day's finds.

**Role toggles.** Below the filters, three chips (Staff, Manager, Director) show or hide jobs by the role level the scorer gave them. All three are on by default; click one to hide that level, and again to bring it back. Hidden levels are left out of every tab and its count. Other levels (architect, senior, other) always show. The choice is kept in the page address as `hide=`, e.g. http://localhost:8080/?hide=staff,director shows only manager-level and other roles, and "Clear filters" leaves it alone.

**Closing dates.** Where a job's closing date is known it shows after "first seen": "apply by 15 Nov" for a deadline the employer states in the advert text (English wording such as "Application deadline:", "Apply by", "Closing date:"), or "ad ends 26 Nov" when the board itself gives an expiry date (Just Join IT, about 30 days after posting unless renewed; Himalayas; Traffit). Dates under two days away are shown in red. Greenhouse, Lever, Ashby, Workable, Personio, Teamtailor, Recruitee, Remote OK, We Work Remotely, Arbeitnow and Hacker News give no closing date, so for those only a deadline written in the advert is found. The **Closing** filter picks jobs closing within 2 or 7 days, or any job with a stated deadline still to come, and the **Closing soonest** sort puts the nearest first, e.g. http://localhost:8080/?closes=2&sort=closing.

Under each job, **Feedback** opens three notes, "Reject for:", "Rate higher for:" and "Rate lower for:", plus "Score higher" and "Score lower" buttons that record which way the scoring weights should have moved for that job. Each note is saved in the `job_feedback` table with a copy of the ad text, all its postings and the score and reasons at the time, so it still makes sense after the job changes or is rescored. A rejection also marks the job not interested. Saved notes show under the job and can be removed with ×. Nothing reads them automatically yet: they are there for tuning the filters and weights later.

**Add job.** The **Add job** link in the header opens a page where you paste any advert's URL. The finder reads what it can and shows the title, company, location, work mode and description for you to check, then **Save and score** stores the job and rescores that company's jobs. How it reads the advert depends on the site:

- LinkedIn and No Fluff Jobs (and Remotive) are never fetched, so the form opens empty for you to paste into. A LinkedIn job is stored under its job id, so it lands on the same job as its email alerts, and the text also counts as a capture.
- On a careers system the finder reads (Greenhouse, Lever, Ashby, Workable, Personio, Recruitee, Teamtailor, Traffit or Comeet), the advert is picked out of the company's feed. A ticked-by-default **Also follow this company's careers page** box starts following the whole feed too, like the Employer link button. A company not stored yet gets a name guessed from its feed address, so check it.
- Just Join IT adverts use the Just Join IT parser.
- Any other page is fetched once, only if its robots.txt allows it, and read from its schema.org JobPosting block, which most careers sites include. If there is none, the site refuses (a 403 or a bot check), or the description is under 200 characters, the form says why and you paste the text. The description is never guessed from the rest of the page.

Jobs added this way are listed as "Added by hand". The URL is the key, without tracking parameters, so adding the same advert again updates it. A job with the same company and title as one already found joins it, and the result page says which happened. Nothing re-checks an advert added by hand, so it never counts as confirming a job's advert is still up, and the employer's own feed still overrides it at scoring.

**Paste description.** An open job with no advert text from any source (most often a LinkedIn alert with no capture) has a **Paste description** button in its details line. It opens a box for the advert text; **Save and rescore** stores it in the `job_descriptions` table and rescores the job at once. Scoring reads it like a posting's own text, and it survives collector runs and job merges. Afterwards the button reads **Replace description** and the box opens with the saved text, so a paste can be corrected.

**The employer's advert is definitive.** A board's copy of an advert can be wrong about where the employer hires (Himalayas listed Ledgebrook's Staff Engineer as open to Poland; Ledgebrook's own page says US or Canada). So when a job also has a posting from the employer's own careers page (any careers-feed collector), scoring uses that advert's location, work mode, text, contract and pay, keeping a board's facts only where the employer's page says nothing; the reasons say "checked against the employer's advert". A board job whose title differs slightly from the employer's ("Sr." vs "Senior") takes the employer's facts the same way ("employer's advert used"). If the employer's page is collected but doesn't list the job at all, its score stands and it gets the chip "not on the employer's careers page: check the advert".

**Employer link.** Himalayas' advert pages can't be fetched (a Cloudflare challenge), so the address behind their Apply button can't be found automatically. Interested Himalayas jobs, and unreviewed Maybes, whose employer's careers page isn't followed yet have an **Employer link** button: open the advert, click Apply, and paste the address it leads to. If it is a careers system the finder reads (Greenhouse, Lever, Ashby, Workable, Personio, Recruitee, Teamtailor, Traffit or Comeet), the company is followed on it from then on, its adverts are collected at once and its jobs rescored. One link covers all the company's jobs. If the page can't be read just then, the company stays followed and the next careers-page run tries again.

To do this in bulk for every job the button is offered on, run once on the machine running the worker (after `docker compose up -d --build`, so the container has the latest code):

```sh
docker compose exec worker python -m vacancy_finder.find_employer_adverts --dry-run   # report only
docker compose exec worker python -m vacancy_finder.find_employer_adverts
```

For each company it looks for the careers page in links in the adverts already collected, then by trying the company's name on each careers system (as the probe does). A page is only taken if it lists one of those jobs under the same title, so a board belonging to a different company with the same name is skipped. Found pages are followed and the company's jobs rescored, and the report shows each job's verdict and score before and after. Jobs it can't place are listed with their Himalayas link for the Employer link button (Comeet pages, for one, can only be found from a link). `--delay` sets the seconds between companies (default 1). A name-based match can still be the wrong company when both the name and the title are common, so check those in the dry run; `--skip "Name"` (repeatable) leaves a company out.

**Aggregators.** Some companies' boards list other companies' vacancies: recruiters, talent networks and job marketplaces such as Huzzle or TechBiz Global. They are worth collecting, but their adverts are no more definitive than Himalayas', so a company can be marked as an aggregator (`companies.aggregator`). Its board is followed as usual, but scoring treats its adverts like any job board's: they override nothing, and jobs missing from them aren't flagged. Where the actual employer's careers page is followed too, the employer's advert wins as before. Mark them when running the script with `--aggregator "Name"` (repeatable), or later with `UPDATE companies SET aggregator = true WHERE name = '…'`. An aggregator's copy of a job is filed under the aggregator's name rather than the client's, so it isn't yet matched to the client's own listing of the same job; comparing advert text would be needed for that.

**Tailor CV.** Jobs on the Interested tab have a **Tailor CV** button. It sends the job's advert URL to the CV Tailor API (`tools/tailor_api.py` in the cv-creator repo) and saves the run in the `job_tailorings` table. Marking a job **Interested** starts this run automatically, once per job: a job that already has a run doesn't start another when marked again, and a job turning interested on its own (a missed advert coming back) starts nothing. Each run is a paid Claude run. If the automatic start fails, the job stays marked and the page says why; the **Tailor CV** button retries. The button then becomes a **CV draft** link to the run's status page, which refreshes itself and links the report and PDF once the run is done (usually a few minutes), plus a **Tailor again** button that forces a fresh run. If the API already has a run for the same URL, that run is linked instead of a new one being started. The worker reaches the API at `TAILOR_API_URL` (by default `http://host.docker.internal:8765`, the host's 127.0.0.1:8765 as seen from Docker Desktop). The links use `TAILOR_BROWSER_URL` (the tailnet address on port 8091), so they open on a laptop or phone too. If the API can't be reached or refuses the run, the page says why.

The rules live in `worker/src/vacancy_finder/matching.py`, written to be read and tuned: role level, Python and SQL, location and work mode, sector, contract and pay add or take away points, and a few things rule a job out entirely (an advert not in English, a requirement to speak Polish or German, a non-engineering role, on-site outside Warsaw, remote limited to other continents, tobacco or gambling).

A few bonuses add up on top: +10 for a job found on Hacker News, +5 for one found in a LinkedIn alert, and +5 at a well-known employer. The well-known employers are listed in `worker/seeds/big_names.csv` (columns `name`, `aliases` separated by `;`), starting with Meta, Apple, Amazon, Netflix and Google. The seeds folder is mounted into the worker, so edits apply at the next scoring without a rebuild. A company matches when its normalised name equals a listed name or alias: case, legal suffixes and country words don't matter ("Google Poland Sp. z o.o." is Google), but the match is exact, so "Apple Leisure Group" isn't Apple. To see which companies in the database match, and which start like a listed name without matching (candidates for an alias):

```sh
docker compose exec worker python -m vacancy_finder.big_names --check
```

## How data is stored

- A **posting** is one advert on one source, unique by `(source, external_id)`.
- A **job** is the real vacancy behind one or more postings. Postings with the same normalized company name and title share a job, so a role advertised on three boards is counted once.
- A posting that disappears from its source after a successful run gets `closed_at` set. A job closes when none of its postings is open.
- After every successful source check, jobs marked interested or applied are re-confirmed against the sources that list every current advert (ATS feeds, careers pages, job boards). When none of those adverts is open any more, `job_reviews.ad_gone_at` is set: an interested job moves to the **Missed** tab, an applied job stays applied and shows "advert gone". If an advert comes back, both are undone. Email alerts only announce adverts, so they can't confirm one is still up or show it has gone: a job known only from alerts is marked "email alerts only" and left alone. Matches, Interested, Applied and Missed jobs show when an advert was last confirmed.
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

The careers-feed collectors poll every company in `worker/seeds/companies.csv` that has an `ats_kind` and `ats_slug`. The file is mounted into the worker, so edits apply on the next round without a rebuild. Columns: `name`, `sectors` (separated by `;`), `ats_kind`, `ats_slug`, `website`, `notes`, `careers_url`, `link_pattern`.

| `ats_kind` | `ats_slug` |
|---|---|
| `greenhouse`, `lever`, `lever-eu`, `ashby`, `workable`, `recruitee`, `traffit` | the company's slug on that system |
| `personio` | the board's host, e.g. `liveeo-gmbh.jobs.personio.de` |
| `teamtailor` | the careers site, e.g. `jobs.tibber.com` |
| `comeet` | the company slug and Comeet's id for it, e.g. `ledgebrook/D9.006` (from any of its advert links; it can't be probed by name) |
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

**Rate limits.** When a site answers 429 Too Many Requests, every collector and checker waits and tries the request again instead of failing: for the site's Retry-After time if it gives one, otherwise 30 seconds, then 60, 120 and 240, never more than 5 minutes at a time. After four retries the request fails as before. The worker log shows each wait ("rate limited (429), waiting …").

### Just Join IT

justjoin.it's robots.txt disallows its API, so the collector reads only what the site offers crawlers: the sitemap of active adverts and each advert's page. To stay polite it opens only adverts whose URL contains a role keyword as a whole word (`JUSTJOINIT_KEYWORDS`), only re-opens an advert when its sitemap date changes, waits `JUSTJOINIT_DELAY_SECONDS` between pages, and opens at most `JUSTJOINIT_MAX_PAGES` per run. The first runs work through the backlog; after that each run fetches only new or changed adverts.

Every posting gets a detected `language` ('en', 'pl' or 'de'). Adverts in Polish or German are kept, so they aren't re-fetched, and are left out at the matching stage.

To check the parser against the live site: `python -m vacancy_finder.check_justjoinit 5`.

No Fluff Jobs disallows automated access to both its API and its advert pages, so it is covered through its email alerts instead (see below).

### Space-Careers

space-careers.com has no feed or API, but its sitemap lists every live advert (about 200) with a last-changed date. The collector reads the sitemap each run and opens only adverts that are new or changed, waiting `SPACECAREERS_DELAY_SECONDS` (2) between pages and opening at most `SPACECAREERS_MAX_PAGES` (300) per run. Adverts give title, company, location, workplace and description, but no pay or closing date. Its alert emails aren't needed, so they are left in the inbox.

### Remote boards

Himalayas (searched with role queries, limited to jobs open to people in Poland), Remote OK, We Work Remotely (programming and DevOps feeds), Arbeitnow (remote jobs and jobs in Poland only) and the Hacker News "Who is hiring?" threads (this month and last, re-read every run so new comments are picked up). These feeds show only recent jobs, so a posting is closed once it has been missing for a couple of weeks (45 days for Hacker News) rather than on its first absence. A posting missing from a run whose expiry date (given by the feed) has passed is closed at once. Himalayas doesn't wait either: a Himalayas posting missing from the role searches is looked up by searching for its company, and closed on that run if the company's results no longer include it (an expired advert's page redirects to the jobs list, but scripts only get a Cloudflare challenge there, so the page itself can't be checked). Companies with Interested or Applied jobs are looked up first, up to `HIMALAYAS_MAX_CHECKS` (40) companies a run; the rest wait for a later run, or fall back to the three-week rule. Remotive is left out: its robots.txt disallows the API.

Settings: `HIMALAYAS_QUERIES` (comma-separated), `HIMALAYAS_MAX_PAGES` (20 jobs per page, per query), `HIMALAYAS_DELAY_SECONDS` (3; Himalayas answers 429 after about 26 requests a minute), `ARBEITNOW_MAX_PAGES`.

To check them against the live sites without touching the database: `python -m vacancy_finder.check_feeds` (or name sources, e.g. `check_feeds himalayas`).

### Job-alert emails

LinkedIn, Welcome to the Jungle (Otta) and No Fluff Jobs are covered through their job-alert emails. No Fluff Jobs hides each link behind a click tracker with the real URL base64-encoded; the parser decodes it. Send the alerts to one inbox and put its login in `.env` as `IMAP_USER` and `IMAP_PASSWORD` (see `.env.example`; the default host is iCloud's `imap.mail.me.com`, which needs an app-specific password). The worker only downloads messages from those sites' senders; the rest of the inbox is never read.

Each alert is kept in the `email_messages` table. An alert that gives at least one job is then marked read and moved to `IMAP_PARSED_FOLDER` ("Parsed job alerts" by default), which the worker creates if it is missing; nothing is deleted. An alert that gives no jobs stays in the inbox, unread, so whatever is left there shows where the parsers need work; once a parser learns to read it, the next run picks it up and moves it. Each run re-reads alerts from the last `EMAIL_ALERT_DAYS` (30), so parser fixes apply to old alerts, and a job closes once its newest alert is older than that.

To see what the parsers make of the inbox without touching the database or the inbox: `set -a; . ../.env; set +a; python -m vacancy_finder.check_email` from the `worker` folder. Add `--save some/folder` to keep the messages as `.eml` files.
