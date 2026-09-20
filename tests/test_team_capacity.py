import pathlib
import re

from werkzeug.security import generate_password_hash

from app.models import Member, Team, db

from .conftest import code_of, csrf_of, join

ROOT = pathlib.Path(__file__).resolve().parents[1]


def team_named(event, name):
    return Team.query.filter_by(session_id=event.id, display_name=name).one()


def set_capacity(event, name, capacity):
    team = team_named(event, name)
    team.capacity = capacity
    db.session.commit()
    return team


def try_join(app, event, team_name, nickname):
    """A fresh browser pressing Join. Returns the response, whatever it is."""
    client = app.test_client()
    token = csrf_of(client)
    return client.post("/join", data={
        "csrf_token": token, "team_code": code_of(event, team_name),
        "nickname": nickname})


def seats(event, name):
    return Member.query.filter_by(team_id=team_named(event, name).id).count()


def test_a_full_team_refuses_a_new_nickname(app, event):
    set_capacity(event, "Team 1", 2)
    assert try_join(app, event, "Team 1", "aiko").status_code in (302, 303)
    assert try_join(app, event, "Team 1", "ren").status_code in (302, 303)

    refused = try_join(app, event, "Team 1", "sora")
    assert refused.status_code == 409
    body = refused.get_data(as_text=True)
    assert "This team is full" in body
    assert "same nickname" in body
    assert seats(event, "Team 1") == 2


def test_a_returning_nickname_gets_its_seat_back_even_when_full(app, event):
    """The whole point of the limit being on NEW names."""
    set_capacity(event, "Team 1", 2)
    try_join(app, event, "Team 1", "aiko")
    try_join(app, event, "Team 1", "ren")

    back = try_join(app, event, "Team 1", "Aiko")      # case does not matter
    assert back.status_code in (302, 303)
    assert seats(event, "Team 1") == 2, "a rejoin must not add a second seat"


def test_one_full_team_does_not_close_another(app, event):
    set_capacity(event, "Team 1", 1)
    set_capacity(event, "Team 2", 1)
    try_join(app, event, "Team 1", "aiko")

    assert try_join(app, event, "Team 1", "ren").status_code == 409
    assert try_join(app, event, "Team 2", "ren").status_code in (302, 303)


def test_no_limit_means_no_limit(app, event):
    assert team_named(event, "Team 3").capacity is None
    for i in range(9):
        assert try_join(app, event, "Team 3", f"m{i}").status_code in (302, 303)
    assert seats(event, "Team 3") == 9


def test_a_refused_browser_is_not_given_a_seat(app, event):
    """Refused means no cookie: the next page it asks for sends it back to /join."""
    set_capacity(event, "Team 1", 1)
    try_join(app, event, "Team 1", "aiko")

    client = app.test_client()
    token = csrf_of(client)
    client.post("/join", data={"csrf_token": token,
                               "team_code": code_of(event, "Team 1"),
                               "nickname": "ren"})
    assert client.get("/team").status_code in (302, 303)


def test_a_ta_is_never_counted_and_never_refused(app, event):
    app.config["ASSISTANT_PASSWORD_HASH"] = generate_password_hash("ta-pass")
    set_capacity(event, "Team 1", 1)
    try_join(app, event, "Team 1", "aiko")
    assert try_join(app, event, "Team 1", "ren").status_code == 409

    ta = app.test_client()
    token = csrf_of(ta)
    assert ta.post("/assistant/login", data={"csrf_token": token,
                                             "password": "ta-pass"}
                   ).status_code in (302, 303)
    team = team_named(event, "Team 1")
    assert ta.post("/assistant/team", data={"csrf_token": token,
                                            "team_id": team.id}
                   ).status_code in (302, 303)
    assert ta.get("/assistant/board").status_code == 200
    assert seats(event, "Team 1") == 1, "the TA took a participant's seat"


def test_the_facilitator_changes_one_teams_limit_on_the_day(app, event,
                                                            facilitator):
    team = set_capacity(event, "Team 1", 1)
    try_join(app, event, "Team 1", "aiko")
    assert try_join(app, event, "Team 1", "ren").status_code == 409

    r = facilitator.post(f"/facilitator/session/team/{team.id}/capacity",
                         {"capacity": 2})
    assert r.status_code == 200 and r.get_json()["capacity"] == 2
    assert try_join(app, event, "Team 1", "ren").status_code in (302, 303)

    r = facilitator.post(f"/facilitator/session/team/{team.id}/capacity",
                         {"capacity": ""})
    assert r.get_json()["capacity"] is None
    assert try_join(app, event, "Team 1", "sora").status_code in (302, 303)


def test_a_nonsense_limit_is_refused(app, event, facilitator):
    team = team_named(event, "Team 1")
    for bad in ("abc", 0, -3, 999):
        r = facilitator.post(f"/facilitator/session/team/{team.id}/capacity",
                             {"capacity": bad})
        assert r.status_code == 400, bad


def test_a_participant_cannot_change_a_limit(app, event):
    p = join(app, event, "Team 1", "kenji")
    team = team_named(event, "Team 1")
    r = p.post(f"/facilitator/session/team/{team.id}/capacity", {"capacity": 50})
    assert r.status_code in (302, 303, 401, 403)
    assert team_named(event, "Team 1").capacity is None


def test_the_briefing_says_to_rejoin_with_the_same_nickname(app, event):
    p = join(app, event, "Team 1", "kenji")
    for lang, fragment in (("en", "same nickname as before"),
                           ("ja", "前回と同じニックネーム")):
        with p.client.session_transaction() as sess:
            sess["lang"] = lang
        assert fragment in p.get("/briefing").get_data(as_text=True), lang


def test_the_seed_script_holds_the_six_table_plan():
    source = (ROOT / "app" / "state.py").read_text(encoding="utf-8")
    names = re.findall(r'"([^"]+)"',
                       re.search(r"TEAM_NAMES = \[(.*?)\]", source, re.S).group(1))
    capacities = [int(n) for n in re.findall(
        r"\d+", re.search(r"TEAM_CAPACITIES = \[(.*?)\]", source, re.S).group(1))]

    assert len(names) == 6
    assert capacities == [4, 4, 4, 5, 5, 5]
    assert sum(capacities) == 27

    from app.state import DEFAULT_TEAM_COUNT, FIXED_TEAM_CODES

    assert DEFAULT_TEAM_COUNT == 6
    assert len(FIXED_TEAM_CODES) == 6 == len(set(FIXED_TEAM_CODES))
