-- Adverts James adds by URL on the results page's Add job page (add_job.py) belong to a source
-- of their own kind, 'manual'. Nothing re-checks them, so like email alerts they can neither
-- confirm an advert is still up nor show it has gone: job_confirmation leaves them out, or a
-- job added by hand would always look listed and hide an employer advert that has closed.
ALTER TABLE sources DROP CONSTRAINT sources_kind_check;
ALTER TABLE sources
    ADD CONSTRAINT sources_kind_check
        CHECK (kind IN ('ats', 'board', 'aggregator', 'email', 'custom', 'manual'));

CREATE OR REPLACE VIEW job_confirmation AS
SELECT p.job_id,
       bool_or(p.closed_at IS NULL) AS still_listed,
       max(p.last_seen_at) AS confirmed_at,
       max(p.closed_at) AS gone_at
FROM postings p
JOIN sources so ON so.id = p.source_id
WHERE so.kind NOT IN ('email', 'manual')
GROUP BY p.job_id;
