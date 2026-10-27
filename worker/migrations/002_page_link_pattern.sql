-- For companies that list jobs on a plain careers page (ats_kind 'page'): a regex that
-- picks the job links out of that page, matched against each link's href as written.
ALTER TABLE companies ADD COLUMN link_pattern text;
