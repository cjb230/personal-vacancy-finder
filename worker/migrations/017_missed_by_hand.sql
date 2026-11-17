-- James can mark an interested job missed himself (the results page's Missed button), for
-- adverts the source check can't see go: a job known only from email alerts, or one a feed
-- still lists after its page has gone. store.confirm_tracked_jobs never moves such a job back to
-- interested; only another decision, or Clear, undoes it.
ALTER TABLE job_reviews ADD COLUMN missed_by_hand boolean NOT NULL DEFAULT false;
