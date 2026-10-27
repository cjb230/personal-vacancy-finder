-- Where postings come from: an ATS family, a job board, an aggregator or an email-alert inbox.
CREATE TABLE sources (
    id          serial PRIMARY KEY,
    key         text NOT NULL UNIQUE,        -- stable code name, e.g. 'greenhouse', 'justjoinit'
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('ats', 'board', 'aggregator', 'email', 'custom')),
    base_url    text,
    enabled     boolean NOT NULL DEFAULT true,
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Employers. Tracked companies with a known ATS feed are polled directly.
CREATE TABLE companies (
    id               serial PRIMARY KEY,
    name             text NOT NULL,
    normalized_name  text NOT NULL UNIQUE,
    website          text,
    sectors          text[] NOT NULL DEFAULT '{}',
    size_hint        text,                    -- free text for now, e.g. '11-50'
    ats_kind         text,                    -- e.g. 'greenhouse', 'lever', 'ashby'
    ats_slug         text,                    -- the company's id on that ATS
    careers_url      text,
    tracked          boolean NOT NULL DEFAULT false,  -- poll its careers feed
    excluded         boolean NOT NULL DEFAULT false,  -- e.g. tobacco, addictive games
    exclusion_reason text,
    notes            text,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ats_kind, ats_slug)
);

-- One real-world vacancy, however many places it is advertised.
CREATE TABLE jobs (
    id               bigserial PRIMARY KEY,
    company_id       integer REFERENCES companies (id),
    title            text NOT NULL,
    fingerprint      text NOT NULL UNIQUE,    -- normalized company + title, used to de-duplicate
    location_text    text,
    work_mode        text NOT NULL DEFAULT 'unknown'
                     CHECK (work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')),
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    excluded         boolean NOT NULL DEFAULT false,
    exclusion_reason text,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now()
);

-- One advert for a job on one source.
CREATE TABLE postings (
    id               bigserial PRIMARY KEY,
    source_id        integer NOT NULL REFERENCES sources (id),
    external_id      text NOT NULL,           -- the source's own id for the advert
    job_id           bigint NOT NULL REFERENCES jobs (id),
    url              text NOT NULL,
    title            text NOT NULL,
    company_name     text NOT NULL,
    location_text    text,
    work_mode        text NOT NULL DEFAULT 'unknown'
                     CHECK (work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')),
    description      text,
    language         text,                    -- ISO 639-1 where known, e.g. 'en', 'pl'
    contract_type    text,                    -- e.g. 'b2b', 'uop', 'permanent', 'contract'
    salary_min       numeric,
    salary_max       numeric,
    salary_currency  text,
    salary_period    text CHECK (salary_period IN ('hour', 'day', 'month', 'year')),
    posted_at        timestamptz,
    raw              jsonb,                   -- the source's original record
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,             -- set when it disappears from its source
    UNIQUE (source_id, external_id)
);

CREATE INDEX postings_job_id_idx ON postings (job_id);
CREATE INDEX jobs_status_last_seen_idx ON jobs (status, last_seen_at DESC);

-- One run of one collector, for monitoring and debugging.
CREATE TABLE collection_runs (
    id             bigserial PRIMARY KEY,
    source_id      integer NOT NULL REFERENCES sources (id),
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'failed')),
    postings_seen  integer NOT NULL DEFAULT 0,
    postings_new   integer NOT NULL DEFAULT 0,
    error          text
);
