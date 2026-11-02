-- Re-confirming the jobs James is tracking. After every successful source check, a job he is
-- interested in or has applied for is checked against the sources that list every current
-- advert (ATS feeds, careers pages, job boards). Email alerts only ever announce adverts, so
-- they can neither confirm one is still up nor show it has gone, and are left out.
--
-- ad_gone_at: when the last such advert for the job disappeared. An interested job whose
-- adverts have gone becomes 'missed'; an applied job keeps its decision. Both are reset if an
-- advert for the job comes back.
ALTER TABLE job_reviews DROP CONSTRAINT job_reviews_decision_check;
ALTER TABLE job_reviews
    ADD CONSTRAINT job_reviews_decision_check
        CHECK (decision IN ('interested', 'not_interested', 'applied', 'missed')),
    ADD COLUMN ad_gone_at timestamptz;

-- Whether each job can be confirmed (it has been seen on a source other than email alerts),
-- whether it still is (one of those adverts is open), and when one was last seen.
CREATE VIEW job_confirmation AS
SELECT p.job_id,
       bool_or(p.closed_at IS NULL) AS still_listed,
       max(p.last_seen_at) AS confirmed_at,
       max(p.closed_at) AS gone_at
FROM postings p
JOIN sources so ON so.id = p.source_id
WHERE so.kind <> 'email'
GROUP BY p.job_id;
