import pytest

from app.models import SESSION_CLOSED, Member, Team, db
from app.state import DEFAULT_TEAM_COUNT, TEAM_PRESETS, add_team, create_session

from .conftest import FACILITATOR_PASSWORD, Participant, join


def facilitator_on(app):
    client = app.test_client()
    client.get("/facilitator/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/facilitator/login",
                data={"csrf_token": token, "password": FACILITATOR_PASSWORD})
    return Participant(client, token, None, "facilitator")


@pytest.mark.parametrize("count", [1, 2, 3, 5, 7, 9, 10])
def test_a_session_can_be_seeded_with_any_number_of_teams(app, count):
    with app.app_context():
        event = create_session(app.config, title=f"{count} teams",
                               code=f"FLEX{count:02d}", team_count=count)
        db.session.commit()
        teams = Team.query.filter_by(session_id=event.id).all()

    assert len(teams) == count
    assert len({t.code for t in teams}) == count, "two teams share a code"
    assert len({t.display_name for t in teams}) == count


def test_the_default_is_a_default_and_not_a_belief(app):
    with app.app_context():
        small = create_session(app.config, title="two", code="FLEXAA",
                               team_count=2)
        big = create_session(app.config, title="nine", code="FLEXBB",
                             team_count=9)
        db.session.commit()
        assert Team.query.filter_by(session_id=small.id).count() == 2
        assert Team.query.filter_by(session_id=big.id).count() == 9
    assert DEFAULT_TEAM_COUNT <= len(TEAM_PRESETS)


def test_a_team_can_be_added_after_the_session_exists(app, event):
    fac = facilitator_on(app)
    with app.app_context():
        before = {t.code for t in Team.query.filter_by(session_id=event.id).all()}

    response = fac.post("/facilitator/session/team", {})
    assert response.status_code == 200, response.data[:200]
    added = response.get_json()["team"]

    with app.app_context():
        after = Team.query.filter_by(session_id=event.id).all()
    assert len(after) == len(before) + 1
    assert before <= {t.code for t in after}
    assert added["code"] not in before


def test_the_added_team_can_be_joined_and_scored(app, event, facilitator):
    """A team added at 13:05 is a team, not a placeholder."""
    fac = facilitator_on(app)
    added = fac.post("/facilitator/session/team", {}).get_json()["team"]
    facilitator.post("/facilitator/mission/digital-footprint/open", {})

    client = app.test_client()
    client.get("/join")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    joined = client.post("/join", data={"csrf_token": token,
                                        "session_code": event.code,
                                        "team_code": added["code"],
                                        "nickname": "late"})
    assert joined.status_code in (302, 303), joined.data[:200]

    member = Participant(client, token, added["code"], "late")
    assert member.get("/mission/digital-footprint").status_code == 200
    saved = member.post("/api/submission",
                        {"mission_slug": "digital-footprint",
                         "question_key": "m1_key_id", "answer": "x",
                         "confidence": "low"}).get_json()
    assert saved["ok"]


def test_it_is_refused_once_the_session_is_closed(app, event):
    fac = facilitator_on(app)
    with app.app_context():
        row = db.session.get(type(event), event.id)
        row.state = SESSION_CLOSED
        db.session.commit()

    response = fac.post("/facilitator/session/team", {})
    assert response.status_code == 404
    assert response.get_json()["error"] == "no_session"


def test_colour_runs_out_rather_than_repeating(app):
    """A seventh team used to be handed team one's colour."""
    with app.app_context():
        event = create_session(app.config, title="ten", code="FLEXCC",
                               team_count=10)
        db.session.commit()
        teams = Team.query.filter_by(session_id=event.id).order_by(Team.id).all()

    coloured = [t.color_key for t in teams if t.color_key]
    assert len(coloured) == len(TEAM_PRESETS)
    assert len(set(coloured)) == len(coloured), "two teams share a colour"
    for team in teams[len(TEAM_PRESETS):]:
        assert team.color_key == "", team.display_name


def test_a_team_without_a_colour_still_renders_everywhere(app, event,
                                                          facilitator):
    fac = facilitator_on(app)
    for _ in range(len(TEAM_PRESETS) + 1 - DEFAULT_TEAM_COUNT + 1):
        fac.post("/facilitator/session/team", {})

    with app.app_context():
        uncoloured = [t for t in Team.query.filter_by(session_id=event.id).all()
                      if not t.color_key]
    assert uncoloured, "expected at least one team past the palette"

    p = join(app, event, uncoloured[0].display_name, "nine")
    assert p.get("/team").status_code == 200
    body = p.get("/team").get_data(as_text=True)
    assert "var(--team-)" not in body


@pytest.mark.parametrize("size", [1, 2, 5, 9])
def test_a_team_of_any_size_reaches_everything(app, event, facilitator, size):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    members = [join(app, event, "Team 1", f"m{i}") for i in range(size)]

    for member in members:
        assert member.get("/mission/digital-footprint").status_code == 200
    assert members[-1].post("/api/submission",
                            {"mission_slug": "digital-footprint",
                             "question_key": "m1_key_id", "answer": "x",
                             "confidence": "low"}).get_json()["ok"]

    with app.app_context():
        team = Team.query.filter_by(session_id=event.id,
                                    display_name="Team 1").one()
        assert Member.query.filter_by(team_id=team.id).count() == size


def test_nothing_asks_how_many_people_a_team_should_have():
    """The engine holds no expected team size — not a constant, not a check."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for banned in ("TEAM_SIZE", "MEMBERS_PER_TEAM", "EXPECTED_MEMBERS"):
            assert banned not in source, f"{path.name} holds {banned}"
