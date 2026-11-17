-- Adverts James pins to a job from the results page (Add link), keyed as postings are, by
-- (source, external_id). A pinned advert always joins that job, whatever its company and title,
-- so a better or newer copy of an advert under a different title (the employer moved it to
-- another careers system and renamed it, say) stays on the job James has already judged. A pin
-- can name a careers-feed advert before that feed is collected; it applies once it is.
CREATE TABLE job_links (
    source_id   integer NOT NULL REFERENCES sources (id),
    external_id text NOT NULL,
    job_id      bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, external_id)
);

CREATE INDEX job_links_job_id ON job_links (job_id);
