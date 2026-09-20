from .conftest import join


def test_the_session_poll_carries_the_announcement_and_its_timestamp(
        app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"announcement": "regroup at 15:20"})
    p = join(app, event, "Team 1", "kenji")
    d = p.get("/api/session-state").get_json()
    assert d["announcement"] == "regroup at 15:20"
    assert d["announcement_at"]            # an ISO stamp — the dismissal token
    facilitator.post("/facilitator/session/state", {"announcement": ""})
    d = p.get("/api/session-state").get_json()
    assert d["announcement"] is None


def test_the_team_page_no_longer_bakes_in_a_static_banner(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"announcement": "IOC board is open"})
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    assert "IOC board is open" not in html
    assert 'id="announce"' not in html


def test_the_popup_is_wired_on_a_participant_page(app, event):
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    assert "/api/session-state" in html
    assert "openModal" in html
    assert "seccap.announce.seen" in html
