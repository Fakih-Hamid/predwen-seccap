from app.models import Member, Observation, Team

from .conftest import code_of, csrf_of, join


def test_join_creates_a_member_in_the_right_team(app, event):
    join(app, event, "Team 1", "kenji")
    with app.app_context():
        team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
        member = Member.query.filter_by(team_id=team.id).one()
        assert member.nickname == "kenji"
        assert member.token


def test_join_needs_a_real_session_code(app, event):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token, "session_code": "NOPE99",
                                     "team_code": code_of(event), "nickname": "x"})
    assert res.status_code == 400


def test_no_session_code_joins_the_open_session(app, event):
    client = app.test_client()
    token = csrf_of(client)
    for omitted in ({}, {"session_code": ""}, {"session_code": "   "}):
        Member.query.delete()
        res = client.post("/join", data=dict(
            {"csrf_token": token, "team_code": code_of(event), "nickname": "x"}, **omitted))
        assert res.status_code in (302, 303), omitted
        assert Member.query.count() == 1, omitted


def test_join_needs_a_real_team_code(app, event):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token, "session_code": event.code,
                                     "team_code": "ZULU", "nickname": "x"})
    assert res.status_code == 400


def test_join_needs_a_nickname(app, event):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token, "session_code": event.code,
                                     "team_code": code_of(event), "nickname": "   "})
    assert res.status_code == 400


def test_several_browsers_join_the_same_team(app, event):
    names = ["aoi", "ren", "yuki", "sora", "mei"]
    for n in names:
        join(app, event, "Team 1", n)
    with app.app_context():
        team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
        assert sorted(m.nickname for m in team.members) == sorted(names)


def test_joining_asks_for_a_nickname_and_assigns_nothing(app, event):
    for n in ["a", "b", "c", "d", "e", "f"]:
        join(app, event, "Team 1", n)
    with app.app_context():
        team = Team.query.filter_by(session_id=event.id,
                                    display_name="Team 1").one()
        assert team.members.count() == 6


def test_the_responsibility_endpoints_are_gone(app, event):
    p = join(app, event, "Team 1", "a")
    assert p.post("/api/role", {"role": "navigator"}).status_code == 404
    assert p.post("/api/rotate", {}).status_code == 404

def test_a_team_cannot_read_another_teams_observations(app, event, facilitator):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    alpha = join(app, event, "Team 1", "alpha-one")
    bravo = join(app, event, "Team 2", "bravo-one")

    alpha.post("/api/observation", {"mission_slug": "digital-footprint",
                                    "text": "alpha found the commit address"})
    bravo.post("/api/observation", {"mission_slug": "digital-footprint",
                                    "text": "bravo is looking at DNS"})

    a = alpha.get("/api/observations").get_json()["observations"]
    b = bravo.get("/api/observations").get_json()["observations"]
    assert [o["text"] for o in a] == ["alpha found the commit address"]
    assert [o["text"] for o in b] == ["bravo is looking at DNS"]


def test_no_endpoint_accepts_a_team_id_from_the_participant(app, event, facilitator):
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("participant."):
            assert "team_id" not in rule.arguments, rule


def test_unjoined_browser_is_sent_to_join(app, event):
    client = app.test_client()
    assert client.get("/team").status_code in (302, 303)
    assert client.get("/api/mission-state?mission_slug=digital-footprint").status_code == 401


def test_leaving_frees_the_seat_but_keeps_the_record(app, event, facilitator):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/observation", {"mission_slug": "digital-footprint", "text": "note"})
    p.post("/leave", {})
    assert p.get("/api/observations").status_code == 401
    with app.app_context():
        assert Observation.query.count() == 1
        assert Member.query.filter_by(nickname="kenji").count() == 1


def _join_fresh_browser(app, event, team, nickname):
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token,
                                     "team_code": code_of(event, team),
                                     "nickname": nickname})
    assert res.status_code in (302, 303), res.data[:300]
    return client


def test_rejoining_with_the_same_nickname_reuses_the_seat(app, event):
    _join_fresh_browser(app, event, "Team 1", "kenji")
    _join_fresh_browser(app, event, "Team 1", "kenji")   # cookie lost, rejoined
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert team.members.count() == 1
    assert Member.query.filter_by(team_id=team.id, nickname="kenji").count() == 1


def test_rejoin_matches_the_nickname_case_insensitively(app, event):
    for nick in ("Kenji", "kenji", "KENJI"):
        _join_fresh_browser(app, event, "Team 1", nick)
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert team.members.count() == 1


def test_different_nicknames_are_still_different_members(app, event):
    join(app, event, "Team 1", "aoi")
    join(app, event, "Team 1", "ren")
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert team.members.count() == 2


def test_a_closed_session_does_not_blame_the_code(app, event):
    from app.state import reset_session

    client = app.test_client()
    token = csrf_of(client)
    reset_session(event)

    res = client.post("/join", data={"csrf_token": token,
                                     "team_code": "ANYTHING", "nickname": "kenji"})
    body = res.data.decode("utf-8")
    assert res.status_code == 400
    assert "No session is open to join" in body
    assert "with that code" not in body


def test_a_typed_session_code_that_names_nothing_still_says_so(app, event):
    """The other half: when a code WAS typed, naming it is the right answer."""
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token, "session_code": "NOPE12",
                                     "team_code": code_of(event), "nickname": "kenji"})
    assert res.status_code == 400
    assert "No session with that code" in res.data.decode("utf-8")
