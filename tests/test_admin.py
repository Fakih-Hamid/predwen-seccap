import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.auth import SESSION_FACILITATOR, SESSION_MEMBER
from app.models import MISSION_CLOSED, MISSION_LOCKED, MISSION_OPEN, Team, db
from app.state import create_session, mission_rows

from .conftest import FACILITATOR_PASSWORD  # noqa: F401  (kept in one place)


def _app(bypass):
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash("rehearsal-only"),
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": bypass,
    })
    return application


@pytest.fixture()
def admin_app():
    application = _app(True)
    with application.app_context():
        db.create_all()
        create_session(application.config, title="Bypass", code="ADMNAA", team_count=5)
        yield application
        db.session.remove()


@pytest.fixture()
def admin(admin_app):
    """A browser that has landed on /admin, with the token its forms carry."""
    client = admin_app.test_client()
    assert client.get("/admin/").status_code == 200
    with client.session_transaction() as sess:
        client.csrf = sess["csrf_token"]
    return client


def act(client, action, **fields):
    return client.post("/admin/act",
                       data=dict(fields, action=action, csrf_token=client.csrf))


def test_admin_does_not_exist_when_the_bypass_is_off():
    application = _app(False)
    assert "admin" not in application.blueprints
    with application.app_context():
        db.create_all()
        client = application.test_client()
        assert client.get("/admin/").status_code == 404
        assert client.post("/admin/act", data={"action": "open_all"}).status_code == 400
        db.session.remove()


def test_the_blueprint_follows_the_flag_and_nothing_else():
    """Registration is the gate. Off means the module is never even loaded."""
    assert "admin" not in _app(False).blueprints
    assert "admin" in _app(True).blueprints


def test_landing_on_admin_signs_the_browser_in_as_facilitator(admin_app):
    client = admin_app.test_client()
    assert client.get("/facilitator/").status_code in (302, 303)
    client.get("/admin/")
    with client.session_transaction() as sess:
        assert sess[SESSION_FACILITATOR] is True
    assert client.get("/facilitator/").status_code == 200


def test_the_panel_still_requires_csrf(admin):
    """The bypass skips the password, not the plumbing."""
    res = admin.post("/admin/act", data={"action": "open_all"})
    assert res.status_code == 400


def test_open_all_and_close_all_drive_the_real_mission_state(admin, admin_app):
    act(admin, "open_all")
    assert {row.state for row in mission_rows(_ev())} == {MISSION_OPEN}

    act(admin, "close_all")
    assert {row.state for row in mission_rows(_ev())} == {MISSION_CLOSED}


def test_relock_rewinds_the_clocks_without_deleting_anything(admin):
    act(admin, "open_all")
    act(admin, "relock")
    rows = mission_rows(_ev())
    assert {row.state for row in rows} == {MISSION_LOCKED}
    assert all(row.opened_at is None and row.paused_seconds == 0 for row in rows)
    assert all(row.remaining_seconds() == row.duration_seconds for row in rows)


def test_sitting_in_a_team_gives_a_real_member_seat_and_reuses_it(admin):
    team = Team.query.filter_by(display_name="Team 1").one()
    act(admin, "sit", team_id=team.id)
    with admin.session_transaction() as sess:
        first = sess[SESSION_MEMBER]
    assert team.members.count() == 1

    act(admin, "sit", team_id=team.id)
    with admin.session_transaction() as sess:
        assert sess[SESSION_MEMBER] == first
    assert team.members.count() == 1, "a second click must not add a second member"

    assert admin.get("/team").status_code == 200
    act(admin, "stand")
    assert admin.get("/team").status_code in (302, 303)


def test_flags_toggle_the_session_switches(admin):
    assert _ev().collective_open is False
    act(admin, "flag", flag="collective_open")
    assert _ev().collective_open is True
    act(admin, "flag", flag="collective_open")
    assert _ev().collective_open is False


def test_an_unknown_flag_changes_nothing(admin):
    before = {f: getattr(_ev(), f) for f in
              ("scoreboard_visible", "collective_open", "final_open", "scores_locked")}
    act(admin, "flag", flag="state")          # a real column, not a switch
    assert {f: getattr(_ev(), f) for f in before} == before


def test_populate_fills_the_console_unevenly_and_is_idempotent(admin):
    from app.admin import SIM_SIZES

    act(admin, "populate")
    teams = Team.query.order_by(Team.id).all()
    counts = [t.members.count() for t in teams]
    assert counts == list(SIM_SIZES[:len(teams)]) == [5, 5, 5, 4, 4]
    assert len(set(counts)) > 1, "every team the same size is not the real room"

    act(admin, "populate")
    assert [t.members.count() for t in Team.query.order_by(Team.id).all()] == counts


def _seated(admin):
    team = Team.query.filter_by(display_name="Team 1").one()
    act(admin, "sit", team_id=team.id)
    return team


def test_a_locked_mission_renders_in_full_under_preview(admin, admin_app):
    _seated(admin)
    slug = mission_rows(_ev())[0].slug
    assert mission_rows(_ev())[0].state == MISSION_LOCKED

    body = admin.get(f"/mission/{slug}").data.decode()
    assert "ADMIN PREVIEW" in body
    assert admin.get(f"/mission/{slug}").status_code == 200
    state = admin.get(f"/api/mission-state?mission_slug={slug}").get_json()
    assert state["mission"]["writable"] is True


def test_a_locked_mission_still_stops_a_real_participant(app, event):
    """The mirror. Same request, no bypass, unchanged behaviour."""
    from .conftest import join
    p = join(app, event)
    slug = mission_rows(event)[0].slug
    body = p.get(f"/mission/{slug}").data.decode()
    assert "ADMIN PREVIEW" not in body
    state = p.get(f"/api/mission-state?mission_slug={slug}").get_json()
    assert state["mission"]["writable"] is False


def test_the_closed_synthesis_and_hidden_scoreboard_open_under_preview(admin):
    _seated(admin)
    ev = _ev()
    assert ev.final_open is False and ev.scoreboard_visible is False

    assert "ADMIN PREVIEW" in admin.get("/final").data.decode()
    assert admin.get("/api/scoreboard").get_json()["visible"] is True
    assert admin.get("/api/session-state").get_json()["final_open"] is True


def test_the_header_offers_every_destination_under_preview(admin):
    _seated(admin)
    assert _ev().collective_open is False

    html = admin.get("/team").data.decode()
    nav = html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
    assert "/collective-intel" in nav
    assert "/final" in nav


def test_preview_does_not_claim_provisional_scores_are_final(admin):
    """`scores_locked` is a fact, not a door. It is reported as it is."""
    _seated(admin)
    assert admin.get("/api/session-state").get_json()["scores_locked"] is False


def test_a_seat_without_the_console_previews_nothing(admin_app, admin):
    _seated(admin)
    assert "ADMIN PREVIEW" in admin.get("/team").data.decode()

    with admin.session_transaction() as sess:
        del sess[SESSION_FACILITATOR]

    assert "ADMIN PREVIEW" not in admin.get("/team").data.decode()
    slug = mission_rows(_ev())[0].slug
    state = admin.get(f"/api/mission-state?mission_slug={slug}").get_json()
    assert state["mission"]["writable"] is False
    assert admin.get("/api/scoreboard").get_json()["visible"] is False


def test_join_offers_one_click_seats_under_preview(admin):
    body = admin.get("/join").data.decode()
    for team in Team.query.all():
        assert team.code in body, "the bypass shows the codes; that is the point"

    team = Team.query.filter_by(display_name="Team 2").one()
    res = act(admin, "sit", team_id=team.id, next="briefing")
    assert res.status_code in (302, 303)
    assert res.headers["Location"].endswith("/briefing")
    assert admin.get("/team").data.decode().count("Team 2") >= 1


def test_join_stays_reachable_with_a_seat_so_teams_can_be_switched(admin):
    _seated(admin)
    assert admin.get("/join").status_code == 200, "preview: /join is the switcher"

    second = Team.query.filter_by(display_name="Team 3").one()
    act(admin, "sit", team_id=second.id)
    assert second.members.count() == 1
    assert Team.query.filter_by(display_name="Team 1").one().members.count() == 1


def test_join_leaks_no_code_and_still_redirects_without_the_bypass(app, event):
    """The mirror, and the assertion that matters most on this page."""
    from .conftest import join

    body = app.test_client().get("/join").data.decode()
    assert event.code not in body
    for team in Team.query.filter_by(session_id=event.id).all():
        assert team.code not in body

    p = join(app, event)
    assert p.get("/join").status_code in (302, 303)


def test_next_is_a_name_from_the_map_and_never_a_url(admin):
    """No open redirect, development build or not."""
    team = Team.query.filter_by(display_name="Team 1").one()
    res = act(admin, "sit", team_id=team.id, next="https://example.invalid/")
    assert res.status_code in (302, 303)
    assert res.headers["Location"].startswith("/admin/"), res.headers["Location"]

    res = act(admin, "sit", team_id=team.id, next="/facilitator/")
    assert res.headers["Location"].startswith("/admin/")


def test_sign_out_drops_both_the_seat_and_the_console(admin):
    team = Team.query.filter_by(display_name="Team 1").one()
    act(admin, "sit", team_id=team.id)
    act(admin, "signout")
    with admin.session_transaction() as sess:
        assert SESSION_MEMBER not in sess
        assert SESSION_FACILITATOR not in sess


def _ev():
    from app.state import active_session
    return active_session()
