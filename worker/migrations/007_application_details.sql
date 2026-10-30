-- When James applied for a job and the folder holding that application (CV, cover letter,
-- notes), asked for when he marks a job applied. The folder is a plain path on his own
-- computer, kept as text; nothing checks it exists.
ALTER TABLE job_reviews
    ADD COLUMN applied_at      timestamptz,
    ADD COLUMN application_dir text;

-- Jobs already marked applied keep the time of that mark as their best guess.
UPDATE job_reviews SET applied_at = updated_at WHERE decision = 'applied';
