import pathlib
import re

import pytest

from .conftest import join

SLUG = "digital-footprint"
CSS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.css"


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def page(p):
    return p.get(f"/mission/{SLUG}").get_data(as_text=True)


def test_the_first_step_is_on_the_page_before_and_after_the_team_starts(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert 'id="firststep"' in page(p)

    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    assert 'id="firststep"' in page(p), "it is reference, not a one-time banner"


def test_it_reads_the_same_on_every_screen(app, event, facilitator):
    """"Got it" used to be one laptop's opinion about a team of five."""
    open_mission(facilitator)
    kenji = join(app, event, "Team 1", "kenji")
    aoi = join(app, event, "Team 1", "aoi")
    kenji.post("/api/observation", {"mission_slug": SLUG, "text": "the updater"})

    assert 'id="firststep"' in page(aoi)
    assert 'id="firststep"' in page(kenji)


def test_a_late_arrival_sees_it(app, event, facilitator):
    open_mission(facilitator)
    join(app, event, "Team 1", "kenji")
    late = join(app, event, "Team 1", "aoi")
    assert 'id="firststep"' in page(late)


def test_nothing_dismisses_it_by_hand_any_more(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "firststepdone" not in html
    assert "seccap.firststep" not in html


def test_it_sits_inside_step_one(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    before, after = html.split('id="firststep"', 1)
    assert before.rsplit("<section", 1)[1].startswith(' class="step" id="step-explore"')
    assert 'id="sources"' in after.split("</section>", 1)[0]


def test_the_side_pane_is_bounded_so_all_of_it_is_reachable(app, event,
                                                            facilitator):
    css = CSS.read_text(encoding="utf-8")
    side = css.split(".work > .side {", 1)[1].split("}", 1)[0]

    assert "position: sticky" in side
    assert "max-height: calc(100vh" in side
    assert "overflow-y: auto" in side


def test_one_column_gets_no_second_scrollbar(app, event, facilitator):
    css = CSS.read_text(encoding="utf-8")
    narrow = css.split("@media (max-width: 1287px) {", 1)[1].split("\n}", 1)[0]

    assert "position: static" in narrow
    assert "max-height: none" in narrow
    assert "overflow: visible" in narrow


def test_the_waiting_screen_says_more_than_not_open(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "While you wait" in html
    assert "/briefing" in html
    assert "kenji" in html, "the team's own seats are not on it"


def test_the_waiting_screen_still_opens_nothing(app, event, facilitator):
    """It is a nicer holding screen, not a way in."""
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "data-answer" not in html
    assert "questionlist" not in html
    r = p.post("/api/submission", {"mission_slug": SLUG,
                                   "question_key": "m1_key_id",
                                   "answer": "SR-REL-2019"})
    assert r.get_json().get("ok") is not True


@pytest.mark.parametrize("lang,fragment", [
    ("en", "While you wait"),
    ("ja", "開始までにできること"),
])
def test_the_waiting_screen_speaks_both_languages(app, event, facilitator, lang,
                                                  fragment):
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in p.get(f"/mission/{SLUG}").get_data(as_text=True)


def test_the_briefing_leads_on_to_the_mission_once_one_is_open(app, event,
                                                               facilitator):
    p = join(app, event, "Team 1", "kenji")
    before = p.get("/briefing").get_data(as_text=True).split("</header>", 1)[1]
    assert "/team" in before
    assert "/lobby" not in before

    open_mission(facilitator)
    after = p.get("/briefing").get_data(as_text=True).split("</header>", 1)[1]
    assert f"/mission/{SLUG}" in after
    assert after.count('class="btn primary"') == 1
