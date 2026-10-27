-- James's notes on individual jobs, kept to improve the filters and scoring weights later.
-- Numbered 005 because 004 is taken by the email-alerts branch.
-- Each row is a snapshot: the ad text, postings and score are copied in when the note is made,
-- so the note still makes sense after the job record changes, closes or is rescored.
CREATE TABLE job_feedback (
    id         bigserial PRIMARY KEY,
    job_id     bigint REFERENCES jobs (id) ON DELETE SET NULL,
    kind       text NOT NULL CHECK (kind IN ('reject', 'rate_higher', 'rate_lower',
                                             'score_higher', 'score_lower')),
    note       text,                          -- "Reject for:", "Rate higher for:" or "Rate lower for:"
    title      text NOT NULL,
    company    text,
    ad_text    text,                          -- description of the most recently seen posting
    postings   jsonb NOT NULL DEFAULT '[]',   -- every posting of the job, with its full description
    verdict    text,                          -- the score at the time
    score      integer,
    role_level text,
    reasons    text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_feedback_job_id ON job_feedback (job_id);
CREATE INDEX job_feedback_kind ON job_feedback (kind, created_at DESC);
