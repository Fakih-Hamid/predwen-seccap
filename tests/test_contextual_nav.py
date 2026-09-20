import re

import pytest

from .conftest import join

SLUG = "digital-footprint"


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def nav_of(p, url="/team"):
    html = p.get(url).get_data(as_text=True)
    return html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]


def links_in(nav):
    return re.findall(r'<a href="([^"]+)"', nav)


def test_the_open_mission_is_in_the_header_and_named(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert f"/mission/{SLUG}" not in links_in(nav_of(p))

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    nav = nav_of(p)
    assert f"/mission/{SLUG}" in links_in(nav)
    assert SLUG not in re.sub(r"<[^>]+>", " ", nav)


def test_with_no_mission_open_it_is_not_a_link(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    nav = nav_of(p)

    assert "tab-off" in nav
    assert "No mission is open" in nav
    assert "/mission/" not in nav


def test_it_follows_the_facilitator_from_one_mission_to_the_next(
        app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    assert f"/mission/{SLUG}" in links_in(nav_of(p))

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    facilitator.post("/facilitator/mission/fake-infrastructure/open", {})
    links = links_in(nav_of(p))
    assert "/mission/fake-infrastructure" in links
    assert f"/mission/{SLUG}" not in links


def test_a_closed_mission_is_not_offered(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    assert "/mission/" not in nav_of(p)
    assert "tab-off" in nav_of(p)


def test_the_class_board_appears_when_it_opens(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert "/collective-intel" not in links_in(nav_of(p))

    facilitator.post("/facilitator/session/state", {"collective_open": True})
    assert "/collective-intel" in links_in(nav_of(p))


def test_the_page_itself_is_never_taken_away(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    for url in ("/collective-intel", "/briefing", "/final",
                "/evidence"):
        assert p.get(url).status_code == 200, url


def test_the_header_is_five_destinations_plus_what_is_open(app, event,
                                                           facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    nav = nav_of(p)

    assert len(links_in(nav)) == 5, links_in(nav)
    for href in ("/team", f"/mission/{SLUG}", "/evidence", "/final", "/briefing"):
        assert href in links_in(nav), href


def test_the_briefing_is_a_tab_not_a_disclosure(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    nav = nav_of(p)

    assert "/briefing" in links_in(nav)
    assert "navmore" not in nav
    assert "<summary>" not in nav


def test_the_language_switch_survives(app, event, facilitator):
    """It is the one control a Japanese-first room must never lose."""
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)

    assert 'class="langbox"' in html
    assert 'value="ja"' in html and 'value="en"' in html


def test_the_skip_link_is_still_first(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    assert html.index('class="skip"') < html.index('<nav class="tabs"')


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Briefing"),
    ("ja", "ブリーフィング"),
    ("en", "No mission is open"),
    ("ja", "現在、取り組めるミッションはありません"),
])
def test_the_header_speaks_both_languages(app, event, facilitator, lang,
                                          fragment):
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in nav_of(p)


def test_the_header_never_says_the_mission_s_word_for_its_sources(app, event,
                                                                  facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, "ja")

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    nav = html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
    assert "参考資料" not in nav
    assert re.search(r">\s*資料\s*<", nav) is None, "the header says 資料 too"


def test_the_facilitator_header_is_untouched(app, event, facilitator):
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    nav = html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
    assert "/facilitator/" in links_in(nav)
    assert "/scoreboard" in links_in(nav)


