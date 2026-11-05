"""Shortening a job's long location list on the results page (no database needed)."""

from vacancy_finder.web import render_locations


def test_short_lists_are_shown_as_they_are():
    assert render_locations("Remote, Europe") == "Remote, Europe"
    assert render_locations(None) == "location not given"


def test_long_lists_keep_poland_and_uk_with_the_rest_on_hover():
    many = ("Berlin, Germany | Paris, France | Warsaw, Poland | Madrid, Spain | "
            "London, United Kingdom | Lisbon, Portugal | Kraków, Poland")
    out = render_locations(many)
    assert ">Warsaw, London, Kraków + other locations<" in out
    assert f'title="{many}"' in out


def test_long_lists_without_poland_or_uk_say_many_locations():
    out = render_locations("Remote: Germany; France; Spain; Italy; Portugal; Netherlands; "
                           "Belgium; Austria; Ireland; Sweden")
    assert ">Many locations<" in out and 'title="Remote: Germany; France' in out


def test_hover_text_is_escaped():
    out = render_locations("<b>Warsaw</b> | " + "Somewhere far away, " * 4)
    assert "<b>" not in out and "&lt;b&gt;Warsaw&lt;/b&gt;" in out
