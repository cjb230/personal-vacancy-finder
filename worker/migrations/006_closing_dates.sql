-- When each advert stops taking applications, where the source or the advert text says.
-- 'expiry' is when a board takes the advert down (Just Join IT, Himalayas, Traffit);
-- 'deadline' is a date the employer gives, read from the advert text by deadlines.py.
ALTER TABLE postings
    ADD COLUMN closes_at   timestamptz,
    ADD COLUMN closes_kind text CHECK (closes_kind IN ('deadline', 'expiry'));

-- Fill in what the stored source records already hold. Text deadlines are filled in by the
-- worker on start (store.fill_deadlines), and every posting is re-read on its next fetch.
UPDATE postings p SET closes_at = (p.raw -> 'job_posting' ->> 'validThrough')::timestamptz,
                      closes_kind = 'expiry'
FROM sources s
WHERE s.id = p.source_id AND s.key = 'justjoinit' AND p.raw -> 'job_posting' ? 'validThrough';

UPDATE postings p SET closes_at = to_timestamp((p.raw ->> 'expiryDate')::double precision),
                      closes_kind = 'expiry'
FROM sources s
WHERE s.id = p.source_id AND s.key = 'himalayas'
  AND jsonb_typeof(p.raw -> 'expiryDate') = 'number';

-- A job's closing time: the earliest employer deadline among its open adverts; failing that,
-- when its last open advert expires, if every one of them has an expiry.
CREATE VIEW job_closing AS
SELECT job_id,
       COALESCE(min(closes_at) FILTER (WHERE closes_kind = 'deadline'),
                CASE WHEN bool_and(closes_at IS NOT NULL) THEN max(closes_at) END) AS closes_at,
       COALESCE(bool_or(closes_kind = 'deadline'), false) AS is_deadline
FROM postings
WHERE closed_at IS NULL
GROUP BY job_id;
