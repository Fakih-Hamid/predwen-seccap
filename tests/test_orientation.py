import re

import pytest

from .conftest import join, open_all_sources, wind_forward

SLUG = "digital-footprint"


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(app, event, facilitator, slug=SLUG):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)


def main_of(html):
    body = html.split("</header>", 1)[-1].split("<footer", 1)[0]
    return re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)


def primaries(html):
    return re.findall(
        r'<(?:a|button)[^>]*class="[^"]*\bprimary\b[^"]*"[^>]*>(.*?)</(?:a|button)>',
        main_of(html), re.S)


@pytest.mark.parametrize("path", ["/", "/join", "/briefing", "/team",
                                  "/artifacts/commit", "/artifacts/token-audit"])
def test_at_most_one_primary_action_per_screen(app, event, facilitator, path):
    """Two filled buttons is two primary actions, which is none."""
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(path).get_data(as_text=True)
    found = primaries(html)
    assert len(found) <= 1, (path, [re.sub(r"<[^>]+>", "", f).strip()
                                    for f in found])


def test_at_most_one_primary_action_per_MISSION_STEP(app, event, facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = main_of(p.get(f"/mission/{SLUG}").get_data(as_text=True))

    steps = re.findall(r'<section class="step"[^>]*data-step="([^"]+)".*?</section>',
                       body, re.S)
    chunks = dict(zip(steps, re.split(r'<section class="step"', body)[1:]))
    for name, chunk in chunks.items():
        found = re.findall(r'class="[^"]*\bbtn\b[^"]*\bprimary\b', chunk)
        assert len(found) <= 1, (name, found)

    outside = re.sub(r'<section class="step".*?</section>', " ", body, flags=re.S)
    found = re.findall(r'class="[^"]*\bbtn\b[^"]*\bprimary\b', outside)
    assert len(found) == 1, ("outside the steps", found)


def test_the_help_buttons_do_not_compete_with_next(app, event, facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    for block in re.findall(r'<details class="qhint".*?</details>', html, re.S):
        assert "primary" not in block
    assert 'class="btn primary" id="stepnext"' in html


def test_the_team_page_says_what_to_do_before_anything_opens(app, event,
                                                             facilitator):
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    body = main_of(html)

    assert "The exercise has not started yet" in body
    assert "Wait for the facilitator to start the first mission" in body
    assert "review the briefing" in body.lower()
    assert "/collective-intel" not in body
    assert "/final" not in body
    assert "/scoreboard" not in body


def test_the_team_page_names_the_open_mission_as_the_next_action(app, event,
                                                                 facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)

    assert len(primaries(html)) == 1
    top = main_of(html).split('class="nextup"', 1)[1].split("</div>\n\n", 1)[0]
    assert "Mission 1" in top
    assert f"/mission/{SLUG}" in top


@pytest.mark.parametrize("flag,link", [
    ("collective_open", "/collective-intel"),
    ("final_open", "/final"),
    ("scoreboard_visible", "/scoreboard"),
])
def test_a_closed_destination_is_offered_only_once_it_opens(app, event,
                                                            facilitator,
                                                            flag, link):
    p = join(app, event, "Team 1", "kenji")
    assert link not in main_of(p.get("/team").get_data(as_text=True))

    facilitator.post("/facilitator/session/state", {flag: True})
    assert link in main_of(p.get("/team").get_data(as_text=True))


def test_the_lobby_is_a_redirect_and_nothing_else(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    r = p.get("/lobby")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/team")

    open_mission(app, event, facilitator)
    assert p.get("/lobby").status_code == 302


def test_naming_the_team_is_on_the_page_a_team_actually_reaches(app, event,
                                                                facilitator):
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    assert 'id="namebox"' in html and 'id="rename"' in html

    r = p.post("/api/team-name", {"name": "Team Kitsune"})
    assert r.get_json()["name"] == "Team Kitsune"
    assert "Team Kitsune" in p.get("/team").get_data(as_text=True)


def test_the_naming_control_disappears_when_it_stops_working(app, event,
                                                             facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert 'id="namebox"' in p.get("/team").get_data(as_text=True)

    open_mission(app, event, facilitator)
    html = p.get("/team").get_data(as_text=True)
    assert 'id="namebox"' not in html
    assert p.post("/api/team-name", {"name": "Too Late"}).status_code == 409


def test_every_artifact_page_offers_help(app, event, facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")

    for artifact in ("commit", "github-account", "dev-notes", "photo-exif",
                     "token-audit", "username-collision"):
        body = main_of(p.get(f"/artifacts/{artifact}").get_data(as_text=True))
        assert "data-stuck" in body, artifact
        assert "data-rung" in body, artifact
        assert f"/mission/{SLUG}#step-explore" not in body, artifact


def test_every_artifact_page_offers_a_way_back(app, event, facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    for artifact in ("commit", "token-audit"):
        html = p.get(f"/artifacts/{artifact}").get_data(as_text=True)
        assert 'class="backbar"' in html, artifact
        assert f'href="/mission/{SLUG}"' in html, artifact


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_no_participant_screen_prints_an_internal_identifier(app, event,
                                                             facilitator, lang):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)

    for path in ("/team", f"/mission/{SLUG}"):
        body = main_of(p.get(path).get_data(as_text=True))
        text = re.sub(r"<[^>]+>", " ", body)
        for slug in ("digital-footprint", "fake-infrastructure",
                     "threat-intelligence"):
            assert slug not in text, (path, lang, slug)


def test_the_mission_says_which_of_the_three_it_is(app, event, facilitator):
    open_mission(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert "Mission 1 of 3" in p.get(f"/mission/{SLUG}").get_data(as_text=True)
