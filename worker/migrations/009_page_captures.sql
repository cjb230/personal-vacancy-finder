-- Advert text James sends from a job page he has open in his own browser (the Capture
-- bookmarklet on the results page), for sites we only hear about through email alerts and
-- never fetch ourselves, such as LinkedIn. Keyed like the alert postings' external_id
-- ("linkedin:4012345678"), so a capture applies whether it arrives before or after the alert.
-- Kept apart from postings because each alert run rewrites its postings without a description.
CREATE TABLE page_captures (
    external_id  text PRIMARY KEY,
    url          text NOT NULL,
    title        text,
    company      text,
    description  text NOT NULL,
    language     text,
    captured_at  timestamptz NOT NULL DEFAULT now()
);
