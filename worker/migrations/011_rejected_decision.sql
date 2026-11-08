-- 'rejected': James applied and the employer turned the application down. Set from an applied
-- job; applied_at and application_dir are kept, and updated_at says when it was marked.
-- Rejected jobs are no longer re-confirmed against their sources (store.confirm_tracked_jobs).
ALTER TABLE job_reviews DROP CONSTRAINT job_reviews_decision_check;
ALTER TABLE job_reviews
    ADD CONSTRAINT job_reviews_decision_check
        CHECK (decision IN ('interested', 'not_interested', 'applied', 'missed', 'rejected'));
