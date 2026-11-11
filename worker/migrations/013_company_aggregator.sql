-- A company whose careers board lists other companies' vacancies too (recruiters, talent
-- networks, job marketplaces such as Huzzle). Its board is still collected, but scoring treats
-- its adverts like any job board's rather than as the employer's own definitive advert.
ALTER TABLE companies ADD COLUMN aggregator boolean NOT NULL DEFAULT false;
