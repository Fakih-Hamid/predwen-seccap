import pathlib

import pytest
import yaml

from app.missions import tx
from app.state import SOURCES_AT_START

from .conftest import join, wind_forward

CONTENT = pathlib.Path(__file__).resolve().parents[1] / "content" / "missions"
SLUG = "digital-footprint"
M1 = ["commit", "github-account", "dev-notes",
      "photo-exif", "token-audit", "username-collision"]


def missions():
    for path in sorted(CONTENT.glob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if tree.get("kind", "mission") == "mission":
            yield path.name, tree


MISSIONS = list(missions())


def member(app, event, facilitator, slug=SLUG):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    return join(app, event, "Team 1", "kenji")


def sources_on(html):
    import re
    found = set(re.findall(r'data-src="([^"]+)"', html))
    return {s for s in found if "CSS.escape" not in s}


def visible(p, slug=SLUG):
    d = p.get(f"/api/mission-state?mission_slug={slug}").get_json()
    return {s["id"] for s in d["sources"]}, d["pending"]


@pytest.mark.parametrize("name,mission", MISSIONS, ids=[n for n, _ in MISSIONS])
def test_every_mission_still_opens_with_something_to_do(name, mission):
    artifacts = mission.get("artifacts") or []
    assert len(artifacts) > SOURCES_AT_START, (
        f"{name} has {len(artifacts)} sources; the staging needs more than the "
        f"{SOURCES_AT_START} it opens with to mean anything")


@pytest.mark.parametrize("name,mission", MISSIONS, ids=[n for n, _ in MISSIONS])
def test_the_first_step_never_names_a_source_that_is_not_there(name, mission):
    import os

    opening = (tx(mission.get("first_step"), "en") or "").lower()
    if not opening:
        pytest.skip("no opening move to check")
    for a in (mission.get("artifacts") or [])[SOURCES_AT_START:]:
        title = (tx(a.get("title"), "en") or "").lower()
        assert not title or title not in opening, (
            f"{name}: first_step points at '{a['id']}', which is not one of the "
            f"first {SOURCES_AT_START}")
        if a.get("path"):
            filename = os.path.basename(a["path"]).lower()
            assert filename not in opening, (
                f"{name}: first_step names the file '{filename}', which belongs "
                f"to '{a['id']}' and is not on the page when the mission opens")


@pytest.mark.parametrize("name,mission", MISSIONS, ids=[n for n, _ in MISSIONS])
def test_no_source_still_carries_the_old_timer(name, mission):
    stale = [a["id"] for a in mission.get("artifacts") or [] if a.get("arrives")]
    assert not stale, f"{name}: {stale} still carry an `arrives` block"


def test_a_source_that_has_not_arrived_is_refused_by_the_server(app, event,
                                                                facilitator):
    p = member(app, event, facilitator)
    assert p.get("/artifacts/username-collision").status_code == 404
    assert p.get("/artifacts/username-collision/download").status_code == 404
    for aid in M1[:SOURCES_AT_START]:
        assert p.get(f"/artifacts/{aid}").status_code == 200, aid


def test_it_is_absent_from_the_page_and_from_every_picker(app, event, facilitator):
    """A title left in a dropdown has already said what is coming."""
    p = member(app, event, facilitator)
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert sources_on(html) == set(M1[:SOURCES_AT_START])
    assert "Profile record collected during triage" not in html
    assert "Not in yet" in html
    assert "Open the sources you already have" in html


def test_opening_the_three_brings_the_fourth_in(app, event, facilitator):
    p = member(app, event, facilitator)
    assert visible(p) == (set(M1[:3]), M1[3])

    p.get(f"/artifacts/{M1[0]}")
    p.get(f"/artifacts/{M1[1]}")
    assert visible(p) == (set(M1[:3]), M1[3]), "two of the three is not all of them"

    p.get(f"/artifacts/{M1[2]}")
    assert visible(p) == (set(M1[:4]), M1[4])

    p.get(f"/artifacts/{M1[3]}")
    assert visible(p) == (set(M1[:5]), M1[5])

    assert p.get(f"/artifacts/{M1[4]}").status_code == 200


def test_it_is_the_team_that_opens_them_not_the_member(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    kenji = join(app, event, "Team 1", "kenji")
    aoi = join(app, event, "Team 1", "aoi")

    kenji.get(f"/artifacts/{M1[0]}")
    aoi.get(f"/artifacts/{M1[1]}")
    aoi.get(f"/artifacts/{M1[2]}")

    assert kenji.get(f"/artifacts/{M1[3]}").status_code == 200
    assert aoi.get(f"/artifacts/{M1[3]}").status_code == 200


def test_one_teams_reading_opens_nothing_for_another(app, event, facilitator):
    """Every team paces itself."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aoi")

    for aid in M1[:SOURCES_AT_START]:
        one.get(f"/artifacts/{aid}")

    assert one.get(f"/artifacts/{M1[3]}").status_code == 200
    assert two.get(f"/artifacts/{M1[3]}").status_code == 404


def test_the_clock_no_longer_brings_anything_in(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG, 1200)
    p = join(app, event, "Team 1", "kenji")

    assert p.get(f"/artifacts/{M1[3]}").status_code == 404
    assert sources_on(p.get(f"/mission/{SLUG}").get_data(as_text=True)) == set(
        M1[:SOURCES_AT_START])


def test_the_poll_reports_it_so_the_page_does_not_need_a_reload(app, event,
                                                                facilitator):
    """Evidence landing mid-investigation must not require pressing F5."""
    p = member(app, event, facilitator)

    before = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert {s["id"] for s in before["sources"]} == set(M1[:SOURCES_AT_START])
    assert before["pending"] == M1[SOURCES_AT_START]

    for aid in M1[:SOURCES_AT_START]:
        p.get(f"/artifacts/{aid}")

    after = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    landed = {s["id"] for s in after["sources"]}
    assert M1[3] in landed
    assert after["pending"] == M1[4]
    card = next(s for s in after["sources"] if s["id"] == M1[3])
    assert card["title"] and card["icon"]
    assert "note" not in card and "expected_pivot" not in card


def test_the_placeholder_is_one_line_and_not_a_queue(app, event, facilitator):
    p = member(app, event, facilitator, "threat-intelligence")
    html = p.get("/mission/threat-intelligence").get_data(as_text=True)

    assert html.count('<div class="pend" data-pending=') == 1
    import re
    block = re.search(r'<div class="pending".*?</div>\s*</div>', html, re.S)
    assert block, "the placeholder block did not render"
    for stale in ("in about", "any moment", "約", "まもなく", "min"):
        assert stale not in block.group(0), f"{stale!r} is still on the placeholder"


def test_the_review_screen_warns_before_an_early_lock(app, event, facilitator):
    p = member(app, event, facilitator)
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert '<div class="pend" data-pending=' in html, (
        "nothing for the review screen to count")
    assert "You have not opened every source yet" in html
    assert "more_coming:" in html, "the review dialog never reads the wording"
    assert f'data-remaining="{len(M1) - SOURCES_AT_START}"' in html


def test_the_polled_card_says_the_same_thing_as_the_rendered_one(app, event,
                                                                 facilitator):
    from app import missions as content_mod

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})   # shows everything

    for lang in ("en", "ja"):
        with p.client.session_transaction() as sess:
            sess["lang"] = lang
        polled = {s["id"]: s for s in
                  p.get(f"/api/mission-state?mission_slug={SLUG}")
                  .get_json()["sources"]}
        with app.app_context():
            definition = content_mod.get_mission(app.config["CONTENT_DIR"], SLUG)
            for entry in definition["artifacts"]:
                full = content_mod.render_artifact_meta(
                    entry, lang, app.config["CONTENT_DIR"])
                card = polled[entry["id"]]
                where = f"{entry['id']} [{lang}]"
                assert card["title"] == full["title"], where
                assert card["icon"] == full["icon"], where
                assert card["tool"] == full["tool"], where
                expected = (full["external"] or {}).get("tool_name")
                assert card["tool_name"] == expected, where


def test_nothing_is_held_back_once_the_mission_is_over(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    assert p.get("/artifacts/username-collision").status_code == 200


def test_running_out_of_time_hands_over_the_rest(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG, 4000)          # past a 3600s mission

    assert p.get("/artifacts/username-collision").status_code == 200


def test_a_locked_mission_shows_nothing_at_all(app, event, facilitator):
    """The staging must not become a way to read a mission before it opens."""
    p = join(app, event, "Team 1", "kenji")
    assert p.get("/artifacts/username-collision").status_code == 404
    assert p.get("/artifacts/commit").status_code == 404
