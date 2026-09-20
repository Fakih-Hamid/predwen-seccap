import re

import pytest

from .conftest import join
from .test_admin import admin, admin_app  # noqa: F401

SLUGS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")
FINAL = "final-incident"
FINAL_HREF = "/final"


@pytest.fixture()
def seated(admin, admin_app):  # noqa: F811
    from app.models import Team

    with admin_app.app_context():
        team_id = Team.query.first().id
    admin.post("/admin/act", data={"action": "sit", "team_id": team_id,
                                   "csrf_token": admin.csrf})
    return admin


def cards(html):
    """slug -> the markup of the card's bottom row."""
    out = {}
    for block in re.findall(r'<div class="panel" data-slug="([^"]+)">(.*?)\n    </div>',
                            html, re.S):
        out[block[0]] = block[1]
    return out


def links_in(html):
    return {slug: re.findall(r'href="([^"]+)"', block)
            for slug, block in cards(html).items()}


def test_previewing_shows_the_whole_inventory_without_waiting(seated):
    body = seated.get("/mission/digital-footprint").get_data(as_text=True)

    for artifact_id in ("commit", "dev-notes", "photo-exif", "token-audit",
                        "username-collision"):
        assert f'data-src="{artifact_id}"' in body, artifact_id
        assert seated.get(f"/artifacts/{artifact_id}").status_code == 200, artifact_id
    assert '<div class="pending"' not in body


def test_a_participant_still_waits_for_the_late_sources(app, event, facilitator):
    """The half that matters more: with no bypass, the gate is the gate."""
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    p = join(app, event, "Team 1", "kenji")
    assert p.get("/artifacts/username-collision").status_code == 404


def test_every_mission_is_reachable_while_previewing(seated):
    """Locked, open or submitted: four cards, four links."""
    html = seated.get("/team").get_data(as_text=True)
    found = links_in(html)

    assert sorted(found) == sorted(SLUGS + (FINAL,)), found
    for slug in SLUGS:
        assert found[slug] == [f"/mission/{slug}"], (slug, found[slug])
    assert found[FINAL] == [FINAL_HREF], found[FINAL]


def test_the_links_actually_open_the_locked_missions(seated):
    for slug in SLUGS:
        html = seated.get(f"/mission/{slug}").get_data(as_text=True)
        assert "questionlist" in html, slug
        assert "While you wait" not in html, slug


def test_a_locked_card_says_preview_rather_than_enter(seated):
    html = seated.get("/team").get_data(as_text=True)
    assert "Preview" in html


@pytest.mark.parametrize("slug", SLUGS)
def test_a_participant_still_cannot_enter_a_locked_mission(app, event,
                                                           facilitator, slug):
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)

    assert links_in(html)[slug] == [], "a locked mission offered a way in"
    assert '<span class="btn sm disabled">' in html
    assert "questionlist" not in p.get(f"/mission/{slug}").get_data(as_text=True)


def test_a_submitted_mission_keeps_the_chip_and_gains_a_way_back_in(app, event,
                                                                    facilitator):
    slug = "digital-footprint"
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/lock", {"mission_slug": slug})

    html = p.get("/team").get_data(as_text=True)
    assert links_in(html)[slug] == [f"/mission/{slug}"]
    assert 'class="chip ok"' in cards(html)[slug]
    assert "View" in cards(html)[slug]
    assert "questionlist" in p.get(f"/mission/{slug}").get_data(as_text=True)


def test_a_closed_mission_is_readable_rather_than_gone(app, event, facilitator):
    """Same promise on the other route in: the facilitator closing a mission."""
    slug = "digital-footprint"
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{slug}/close", {})

    html = p.get("/team").get_data(as_text=True)
    assert links_in(html)[slug] == [f"/mission/{slug}"]
    assert "View" in cards(html)[slug]
    assert "Not open yet" not in cards(html)[slug]
    assert "questionlist" in p.get(f"/mission/{slug}").get_data(as_text=True)


def test_an_open_mission_says_enter_for_a_participant(app, event, facilitator):
    slug = "digital-footprint"
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    p = join(app, event, "Team 1", "kenji")

    html = p.get("/team").get_data(as_text=True)
    assert links_in(html)[slug] == [f"/mission/{slug}"]
    assert "Enter" in cards(html)[slug]
    assert "Preview" not in html, "the preview label leaked into a real render"
