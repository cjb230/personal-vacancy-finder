-- 'gone': the advert disappeared before James judged the job (marked by hand from the results
-- page). Kept apart from 'not_interested' so a classifier trained on his decisions later does
-- not read a job he never saw as one he turned down. Gone jobs are not re-confirmed against
-- their sources (store.confirm_tracked_jobs only looks at interested and applied jobs).
ALTER TABLE job_reviews DROP CONSTRAINT job_reviews_decision_check;
ALTER TABLE job_reviews
    ADD CONSTRAINT job_reviews_decision_check
        CHECK (decision IN ('interested', 'not_interested', 'applied', 'missed', 'rejected',
                            'gone'));
