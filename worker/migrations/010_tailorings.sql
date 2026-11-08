-- CV tailoring runs started from the results page, one row per run (see tailor-api.md in the
-- cv-creator repo). The Tailor API keeps the run itself; this keeps which job it was for and the
-- browser link to its status page, which links the report and the PDF once the run is done.
CREATE TABLE job_tailorings (
    id            bigserial PRIMARY KEY,
    job_id        bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    tailor_job_id text NOT NULL,              -- the API's job id, e.g. 20261108-172443-5533
    ad_url        text NOT NULL,              -- the posting URL sent to the API
    status        text,                       -- as returned when the run was started
    status_url    text NOT NULL,              -- the run's status page, for a browser on the tailnet
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, tailor_job_id)
);
CREATE INDEX job_tailorings_job_id ON job_tailorings (job_id, created_at DESC);
