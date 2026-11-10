-- Advert text James pastes on the results page for a job that has none (the "Paste
-- description" button). Kept per job, apart from postings, because each collector run
-- rewrites its postings; scoring reads it alongside the postings' own text.
CREATE TABLE job_descriptions (
    job_id      bigint PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    description text NOT NULL,
    language    text,
    pasted_at   timestamptz NOT NULL DEFAULT now()
);
