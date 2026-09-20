import re

from app.models import Member, Team

from .conftest import code_of, csrf_of, team_of


def teams_of(app, event):
    with app.app_context():
        return {t.display_name: t.id for t in
                Team.query.filter_by(session_id=event.id).order_by(Team.id)}


def test_the_page_lists_team_names_and_never_a_code(app, event):
    client = app.test_client()
    html = client.get("/join").get_data(as_text=True)
    for name in ("Team 1", "Team 2", "Team 5"):
        assert name in html, name
    assert 'name="team_id"' in html
    with app.app_context():
        codes = [t.code for t in Team.query.filter_by(session_id=event.id)]
    for code in codes:
        assert code not in html, code
    assert 'name="session_code"' not in html


def test_no_session_open_shows_a_message_and_no_form(app):
    """A fresh app with no session: nothing to join, said plainly."""
    from app import create_app
    from app.models import db
    from werkzeug.security import generate_password_hash

    application = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test", "DEFAULT_LANG": "en", "ADMIN_BYPASS": False,
        "FACILITATOR_PASSWORD_HASH": generate_password_hash("x"),
    })
    with application.app_context():
        db.create_all()
        html = application.test_client().get("/join").get_data(as_text=True)
        assert "No session is open to join yet" in html
        assert "Wait for the facilitator" in html
        assert 'name="team_id"' not in html
        db.session.remove()


def test_clicking_a_team_and_typing_its_code_joins_it(app, event):
    client = app.test_client()
    token = csrf_of(client)
    ids = teams_of(app, event)

    res = client.post("/join", data={
        "csrf_token": token, "team_id": ids["Team 3"],
        "team_code": code_of(event, "Team 3"), "nickname": "kenji"})
    assert res.status_code in (302, 303)

    with app.app_context():
        team = team_of(event, "Team 3")
        member = Member.query.filter_by(team_id=team.id).one()
        assert member.nickname == "kenji"


def test_the_code_must_belong_to_the_team_you_picked(app, event):
    client = app.test_client()
    token = csrf_of(client)
    ids = teams_of(app, event)

    res = client.post("/join", data={
        "csrf_token": token, "team_id": ids["Team 3"],
        "team_code": code_of(event, "Team 2"), "nickname": "kenji"})
    assert res.status_code == 400
    assert Member.query.count() == 0


def test_a_wrong_code_is_refused(app, event):
    client = app.test_client()
    token = csrf_of(client)
    ids = teams_of(app, event)
    res = client.post("/join", data={
        "csrf_token": token, "team_id": ids["Team 1"],
        "team_code": "NOPE9", "nickname": "kenji"})
    assert res.status_code == 400
    assert Member.query.count() == 0


def test_a_bogus_team_id_falls_back_to_the_code(app, event):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={
        "csrf_token": token, "team_id": 999999,
        "team_code": code_of(event, "Team 4"), "nickname": "sora"})
    assert res.status_code in (302, 303)
    with app.app_context():
        assert Member.query.filter_by(
            team_id=team_of(event, "Team 4").id).one().nickname == "sora"


def test_the_old_session_code_path_still_joins(app, event):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={
        "csrf_token": token, "session_code": event.code,
        "team_code": code_of(event, "Team 2"), "nickname": "aoi"})
    assert res.status_code in (302, 303)
    with app.app_context():
        assert Member.query.filter_by(
            team_id=team_of(event, "Team 2").id).one().nickname == "aoi"


def test_the_page_carries_no_role_field(app, event):
    html = app.test_client().get("/join").get_data(as_text=True)
    form = html.split('<form method="post" class="panel">', 1)[1].split("</form>", 1)[0]
    assert 'name="role"' not in form
