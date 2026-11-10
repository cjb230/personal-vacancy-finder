# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal job-search tool. It collects vacancies from many sources into Postgres, scores each job against one person's criteria, and serves a results page. `README.md` is the user-facing guide and `docs/job-sources.md` has the source map, the search criteria and the planned build order. Read those before adding a source or changing behaviour.

## Commands

All Python work happens in `worker/` (Python 3.15 per `.python-version`, managed with uv):

```sh
cd worker
uv sync                                          # create .venv with dev tools
uv run pytest                                    # unit tests (DB tests skip)
uv run pytest tests/test_matching.py::test_name  # a single test
TEST_DATABASE_URL=postgresql://... uv run pytest # include DB tests
```

- DB tests (`*_db.py`, `test_store.py`, `test_web.py`, `test_renormalize.py`) are skipped unless `TEST_DATABASE_URL` is set. They run `DROP SCHEMA public CASCADE` first, so point them only at a disposable database, e.g. `docker compose exec db createdb -U vacancies vacancies_test`.
- After changing dependencies in `pyproject.toml`, run `uv lock` and commit `uv.lock`. The Docker build installs with `uv sync --locked`, so a stale lockfile breaks it.
- No linter or formatter is configured.
- To run the stack: `docker compose up -d --build`, then `docker compose logs -f worker`. This needs `.env` (copy `.env.example`, set `POSTGRES_PASSWORD`).

Live-check tools fetch from real sites without touching the database (run them from `worker/`):
- `python -m vacancy_finder.check_feeds [source ...]`: remote boards.
- `python -m vacancy_finder.check_justjoinit N`: the Just Join IT parser.
- `python -m vacancy_finder.check_companies [--kind page] [NAME ...]`: seeded careers feeds.
- `python -m vacancy_finder.probe "Company" ...` or `--file seeds/candidates.txt`: finds a company's ATS slug and prints rows for `seeds/companies.csv`.

## Architecture

The worker is a single process (`vacancy_finder/__main__.py`). On start it applies migrations, then runs `renormalize` (re-keys companies and jobs if `normalize.py` rules changed), `fill_deadlines` (re-parses deadlines if wording rules changed) and `score_all` (rescores if `matching.py` changed). All three are idempotent and run on every start, so a rule change takes effect on the next restart without a backfill script. The web server then starts in a daemon thread, and the loop begins: reload `seeds/companies.csv`, run every collector, rescore, sleep `RUN_INTERVAL_MINUTES`.

**Collectors** (`collectors/`) implement the `Collector` protocol in `base.py`. `collect(conn)` yields `RawPosting`s (`models.py`) and `incomplete_company_ids()` names the companies whose fetch failed. Register a new collector in `COLLECTORS` in `collectors/__init__.py`. Per-company ATS collectors subclass `AtsCollector` (`ats.py`) and override `feed_url` and `parse`. `parse` is pure (payload in, postings out), and unit tests exercise it against saved payloads in `tests/fixtures/`. `PageCollector` scrapes plain careers pages using each company's `careers_url` and `link_pattern` from the seed file. Collectors that read a board feed live in `justjoinit.py` and `remote.py`.

**Run bookkeeping** (`runner.py`) logs each run in `collection_runs`. After a successful pass it closes postings that weren't seen (`close_unseen`), skipping companies listed in `incomplete_company_ids()`. A collector can set `stale_after_days` for feeds that show only recent jobs. A failed run rolls back and closes nothing.

**Data model and de-duplication** (`store.py`, `normalize.py`): a posting is unique by `(source, external_id)`. Postings share a job when `job_fingerprint(company, title)` matches, using normalised company names (legal suffixes dropped, diacritics folded) and normalised titles (gender markers, work mode and trailing place names dropped). `unique_job=True`, or an unknown company, stops a posting merging by title. `upsert_posting` also detects the advert's language and pulls a stated deadline from the text (`deadlines.py`). A deadline found in the text overrides a board's own expiry date.

**Scoring** (`matching.py`) is deliberately plain rules: regexes and point values, each adding a human-readable reason. Hard rule-outs (non-English advert, Polish or German required, non-engineering role, location ruled out, tobacco or gambling) force the verdict `no`. Otherwise `MATCH`/`MAYBE` thresholds pick the tab. Pay is compared as monthly take-home PLN, with B2B and UoP (Polish employment contract) taxes modelled, so neither contract type gets a bonus. Scores land in `job_scores`. User decisions and feedback (`job_feedback`, which keeps a snapshot of the ad and its score) are kept separately, so they survive rescoring.

**Web** (`web.py`) uses only the stdlib `ThreadingHTTPServer` and renders HTML by hand, with no framework or templates. Filters are query-string parameters, so URLs can be bookmarked.

**Migrations**: add numbered `.sql` files to `worker/migrations/` (use the next free number). `migrate.py` applies each file once, in its own transaction, recorded in `schema_migrations`. Never edit a migration that has already been applied.

## Conventions

- Be polite to sources: respect robots.txt and use `USER_AGENT` from `collectors/ats.py`. Sources whose robots.txt or terms forbid automated access (No Fluff Jobs, Remotive, LinkedIn and similar) are covered through email alerts or left out, never scraped. Check before adding a source.
- Keep Polish and German adverts in the database, marked with `language`, so they aren't fetched again. They are excluded at scoring, not at collection.
- `worker/seeds/` is mounted read-only into the container, so seed edits apply on the next cycle without a rebuild. Code changes need `docker compose up -d --build`.
- Docs, docstrings and commit messages use plain English, and commit subjects are short imperative sentences with no type prefixes. Update `README.md` whenever a change affects user-visible behaviour (sources, results page, settings).
- `tailor-api.md` (if present) is the contract for the separate CV-tailoring API. Each run costs money, so only start one when a person explicitly asks for it.
