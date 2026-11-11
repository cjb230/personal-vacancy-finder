from vacancy_finder.discover import find_boards


def test_finds_each_ats_board():
    text = """
    Apply: https://job-boards.greenhouse.io/planetlabs/jobs/123 or
    https://boards.greenhouse.io/embed/job_app?token=9&for=spire,
    https://jobs.lever.co/Nomagic/abc-def, https://jobs.eu.lever.co/acme-eu/1,
    https://jobs.ashbyhq.com/iceye/5a1b?utm=hn, https://apply.workable.com/exotec/j/ABC/,
    https://agile-robots.workable.com/, https://LiveEO-GmbH.jobs.personio.de/job/7,
    https://up42gmbh.recruitee.com/o/eng, https://cloudferro.traffit.com/public/,
    https://acme.teamtailor.com/jobs/1
    """
    assert find_boards(text) == {
        ("greenhouse", "planetlabs"), ("greenhouse", "spire"),
        ("lever", "Nomagic"), ("lever-eu", "acme-eu"),
        ("ashby", "iceye"), ("workable", "exotec"), ("workable", "agile-robots"),
        ("personio", "liveeo-gmbh.jobs.personio.de"), ("recruitee", "up42gmbh"),
        ("traffit", "cloudferro"), ("teamtailor", "acme.teamtailor.com"),
    }


def test_ignores_the_ats_own_pages():
    text = "https://www.workable.com/ https://apply.workable.com/api/v1 https://app.recruitee.com/"
    assert find_boards(text) == set()


def test_unquotes_ashby_slugs():
    assert find_boards("https://jobs.ashbyhq.com/Open%20Cosmos/1") == {("ashby", "Open Cosmos")}


def test_empty_text():
    assert find_boards(None) == set()


def test_finds_comeet_boards_from_an_advert_link():
    link = ("https://www.comeet.com/jobs/ledgebrook/D9.006/staff-engineer/CA.17C"
            "?utm_source=himalayas.app&ref=himalayas.app")
    assert find_boards(link) == {("comeet", "ledgebrook/D9.006")}
    assert find_boards("https://www.comeet.com/jobs/ledgebrook/D9.006") == {
        ("comeet", "ledgebrook/D9.006")}
    assert find_boards("https://www.comeet.com/resources/blog") == set()
