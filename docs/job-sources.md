# Job sources map (step 1)

Updated 2026-10-26 with James's criteria: based in Poland; fully remote (EU companies), hybrid Warsaw (max 2 days/week), or hybrid further afield (max 2 travel days/month). Roles: engineering manager/lead, staff engineer, director where it doesn't demand prior director experience. Sectors: space (incl. satellite imagery, GIS) first; then non-fossil energy and energy markets, health, edtech, green tech, robotics. Prefers B2B with Polish employers; strong UoP also worth surfacing. Core tech: Python and relational databases. English only, so Polish-language adverts are dropped. Prefers smaller companies. Sites used now: Otta (Welcome to the Jungle), LinkedIn, Just Join IT.

Access methods marked "to verify" get checked when we build each collector.

## Tier 1: company careers pages via ATS APIs (build first)

Most companies host their careers page on an applicant tracking system (ATS) with a free, public, no-login JSON feed. This is the most complete and accurate data, straight from the employer.

| ATS | Public endpoint (per company) | Notes |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true` | Tech and scale-ups |
| Lever | `api.lever.co/v0/postings/{company}?mode=json` | Startups |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{company}` | Startups, growing fast |
| Workable | `apply.workable.com/api/v1/widget/accounts/{company}` | SMEs, strong in EU |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{company}/postings` | Larger companies |
| Recruitee, Personio, Teamtailor | Public XML/JSON feeds per company | Very common with EU and Polish companies |
| Workday | Undocumented JSON behind each careers site | Big corporates; fiddlier |
| Custom pages | Page-change detection | For companies with no ATS, e.g. small Polish space firms |

This tier needs a **company list**: seed it from the sector lists below, then grow it automatically from every employer seen in Tier 2 and 3.

## Tier 2: Polish tech boards (core for B2B roles)

| Source | Access | Notes |
|---|---|---|
| Just Join IT | Public JSON behind the site (to verify) | Largest Polish IT board, shows B2B rates |
| No Fluff Jobs | Public JSON behind the site (to verify) | Always shows salary, B2B vs UoP |
| theprotocol.it | Site search (to verify) | Pracuj group's IT board |
| Bulldogjob | Site search / RSS (to verify) | Polish IT |
| Pracuj.pl | Saved-search email alerts | Low priority: mostly Polish-language adverts |

## Tier 3: EU and remote aggregators

| Source | Access | Notes |
|---|---|---|
| Adzuna | Free API with key | Covers Poland and most of EU |
| Jooble, Careerjet | Free / partner API | Aggregators, cover Poland |
| Arbeitnow | Free public API | EU, includes remote |
| Himalayas, Remotive, RemoteOK | Free public APIs | Remote-only, can filter for EU timezones |
| We Work Remotely, Working Nomads | RSS | Remote-only |
| Hacker News "Who is hiring" | Free Algolia API | Monthly, many remote EU roles |
| EURES (EU job portal) | Public search (to verify) | Official EU-wide board |

## Tier 4: sector boards and company lists

**Space (top priority)**

Company names below are a seed from memory, not yet checked; each gets verified (still exists, has a careers page) when added to the database.

- ESA careers, EUMETSAT, EUSPA (EU Space Programme Agency), Polish Space Agency (POLSA).
- Space boards: space-careers.com, SpaceCrew, Space Individuals, SpaceTalent.
- Polish space companies to seed Tier 1: Creotech Instruments, KP Labs, SatRevolution, Scanway, Astronika, PIAP Space, Sener Poland, ICEYE Polska, CloudFerro (EO data platforms), Spacive, Thorium Space, SatAgro. Plus EU players: ICEYE, Planet, Airbus Defence and Space, OHB, Thales Alenia Space, Exotrail, Open Cosmos, Unseenlabs, D-Orbit, Isar Aerospace, The Exploration Company.
- Downstream EO and GIS: Up42, EarthDaily, Satellogic, Sentinel Hub (Planet), Esri distributors, GIS boards.

**Green tech and energy:** Climatebase, Work on Climate, Terra.do job board, ClimateTechList, Green Jobs Board. Polish/EU energy-market companies to seed later.

**Health, edtech, robotics:** sector lists via Welcome to the Jungle, Wellfound and investor portfolios; robotics companies seeded from industry lists.

## Tier 5: big boards without open APIs

LinkedIn, Indeed, Glassdoor, Welcome to the Jungle, Wellfound. No public API, and their terms forbid scraping. Plan: **saved-search email alerts** sent to a dedicated inbox, which the worker parses. Allowed, robust, and covers the sites you already use.

## Filtering at collection time

Hard exclusions are applied early so they don't clutter the database: adverts written in Polish, tobacco and addictive "free-to-play" gaming companies, roles needing on-site more than the location rules allow, roles outside the EU without remote. Everything else is kept and scored later (step 4).

## Suggested build order for step 3

1. Greenhouse, Lever, Ashby, Workable, Recruitee/Teamtailor/Personio collectors, seeded with the space company list.
2. Just Join IT (you use it already) and No Fluff Jobs, for Polish B2B roles.
3. Adzuna plus remote boards, for breadth and new companies.
4. Email-alert parser for LinkedIn and Welcome to the Jungle (Otta).
5. Sector boards (space-careers.com, Climatebase) and custom-page watchers.
