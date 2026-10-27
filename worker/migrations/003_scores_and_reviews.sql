-- How well each job fits James's criteria (written by matching.py after every cycle),
-- and his own decisions, kept separate so rescoring never overwrites them.
CREATE TABLE job_scores (
    job_id     bigint PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    verdict    text NOT NULL CHECK (verdict IN ('match', 'maybe', 'no')),
    score      integer NOT NULL,
    role_level text,                          -- director | manager | staff | senior | other
    reasons    text[] NOT NULL DEFAULT '{}',  -- why, in plain words, best first
    scored_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_scores_verdict_score ON job_scores (verdict, score DESC);

CREATE TABLE job_reviews (
    job_id     bigint PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    decision   text NOT NULL CHECK (decision IN ('interested', 'not_interested', 'applied')),
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
