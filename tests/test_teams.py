from datetime import timedelta

from app import missions as content
from app.models import ScoreEvent, Team, aware, db, utcnow
from app.state import TEAM_CODE_LENGTH, names_locked, normalise_team_name

from .conftest import join, team_of

SLUG = "digital-footprint"


def test_presence_is_not_written_on_every_poll(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    member = team_of(event, "Team 1").members.one()

    first = aware(member.last_seen_at)
    for _ in range(6):                       # six polls, well inside the threshold
        p.get(f"/api/mission-state?mission_slug={SLUG}")
    db.session.refresh(member)
    assert aware(member.last_seen_at) == first, "presence was rewritten mid-window"


def test_presence_is_written_once_the_threshold_passes(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    member = team_of(event, "Team 1").members.one()

    stale = utcnow() - timedelta(seconds=app.config["PRESENCE_SECONDS"] + 5)
    member.last_seen_at = stale
    db.session.commit()

    p.get(f"/api/mission-state?mission_slug={SLUG}")
    db.session.refresh(member)
    assert aware(member.last_seen_at) > stale


def test_the_threshold_can_never_be_smaller_than_the_poll(app):
    from app.config import Config
    assert Config.PRESENCE_SECONDS >= 2 * Config.POLL_SECONDS


def test_the_online_window_leaves_room_for_a_throttled_write(app):
    assert (app.config["ONLINE_WINDOW_SECONDS"]
            > app.config["PRESENCE_SECONDS"] + app.config["POLL_SECONDS"])


def test_a_member_polling_normally_stays_online(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    join(app, event, "Team 1", "kenji")
    member = team_of(event, "Team 1").members.one()
    member.last_seen_at = utcnow() - timedelta(
        seconds=app.config["PRESENCE_SECONDS"] + app.config["POLL_SECONDS"])
    db.session.commit()
    assert member.online is True


def test_team_codes_are_generated_not_guessable(app, event):
    codes = [t.code for t in Team.query.filter_by(session_id=event.id).all()]
    from app.state import DEFAULT_TEAM_COUNT
    assert len(codes) == DEFAULT_TEAM_COUNT
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert len(code) == TEAM_CODE_LENGTH
        assert code not in ("ALPHA", "BRAVO", "CIDER", "DELTA", "ECHO")
        assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_two_sessions_share_the_fixed_team_codes(app):
    from app.state import FIXED_TEAM_CODES, create_session
    a = create_session(app.config, code="AAAAAA", team_count=6)
    b = create_session(app.config, code="BBBBBB", team_count=6)
    ca = {t.code for t in Team.query.filter_by(session_id=a.id).all()}
    cb = {t.code for t in Team.query.filter_by(session_id=b.id).all()}
    assert ca == cb == set(FIXED_TEAM_CODES)


def test_the_landing_page_does_not_print_the_session_code(app, event):
    body = app.test_client().get("/").data.decode("utf-8")
    assert event.code not in body


def test_the_join_form_does_not_prefill_the_session_code(app, event):
    body = app.test_client().get("/join").data.decode("utf-8")
    assert event.code not in body


def test_a_wrong_team_code_is_refused(app, event):
    from .conftest import csrf_of
    client = app.test_client()
    token = csrf_of(client)
    res = client.post("/join", data={"csrf_token": token, "session_code": event.code,
                                     "team_code": "ZZZZZ", "nickname": "x"})
    assert res.status_code == 400


def test_the_scoreboard_is_not_public(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    anon = app.test_client()
    assert anon.get("/scoreboard").status_code in (302, 303)
    assert anon.get("/api/scoreboard", headers={"Accept": "application/json"}) \
        .status_code == 401


def test_participants_and_the_facilitator_can_read_the_scoreboard(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    assert p.get("/scoreboard").status_code == 200
    assert p.get("/api/scoreboard").get_json()["visible"] is True
    assert facilitator.get("/scoreboard").status_code == 200


def test_a_member_of_a_closed_session_cannot_read_the_live_one(app, event, facilitator):
    """A member sees THEIR session, never `active_session()`."""
    from app.state import create_session
    p = join(app, event, "Team 1", "kenji")
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})

    later = create_session(app.config, code="LATER1", team_count=5)
    later.scoreboard_visible = False
    db.session.commit()

    assert p.get("/api/scoreboard").get_json()["visible"] is True
    assert facilitator.get("/api/scoreboard").get_json()["visible"] is False


def test_the_scoreboard_carries_one_column_per_mission_plus_the_final(app, event, facilitator):
    team = team_of(event, "Team 1")
    db.session.add_all([
        ScoreEvent(session_id=event.id, team_id=team.id, mission_slug="digital-footprint",
                   source="auto", category="correctness", points=90, actor="server"),
        ScoreEvent(session_id=event.id, team_id=team.id, mission_slug="final-incident",
                   source="auto", category="correctness", points=130, actor="server"),
    ])
    db.session.commit()
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})

    data = join(app, event, "Team 1", "kenji").get("/api/scoreboard").get_json()
    assert [c["slug"] for c in data["columns"]] == [
        "digital-footprint", "fake-infrastructure", "threat-intelligence", "final-incident"]
    assert [c["label"] for c in data["columns"]] == ["M1", "M2", "M3", "M4"]

    row = next(r for r in data["rows"] if r["name"] == "Team 1")
    assert row["by_mission"]["digital-footprint"] == 90
    assert row["by_mission"]["final-incident"] == 130
    assert row["total"] == 220
    assert "fake-infrastructure" not in row["by_mission"]      # nothing scored yet


def test_the_ranking_is_deterministic_and_documented(app, event, facilitator):
    from app.scoring import leaderboard
    one, two = team_of(event, "Team 1"), team_of(event, "Team 2")
    db.session.add_all([
        ScoreEvent(session_id=event.id, team_id=one.id, source="auto",
                   category="correctness", points=40, actor="server"),
        ScoreEvent(session_id=event.id, team_id=two.id, source="auto",
                   category="correctness", points=30, actor="server"),
        ScoreEvent(session_id=event.id, team_id=two.id, source="auto",
                   category="evidence", points=10, actor="server"),
    ])
    db.session.commit()
    first = [r["name"] for r in leaderboard(event)]
    assert [r["name"] for r in leaderboard(event)] == first     # stable
    assert first[0] == "Team 2"


def test_hints_are_not_a_hidden_tiebreak(app, event, facilitator):
    from app.scoring import charge_hint, leaderboard
    one, two = team_of(event, "Team 1"), team_of(event, "Team 2")
    for team in (one, two):
        db.session.add(ScoreEvent(session_id=event.id, team_id=team.id, source="auto",
                                  category="evidence", points=20, actor="server"))
    db.session.commit()

    definition = content.get_mission(app.config["CONTENT_DIR"], "digital-footprint")
    for hint in definition["hints"][:4]:
        charge_hint(event, one, "digital-footprint", hint)

    rows = {r["name"]: r for r in leaderboard(event)}
    assert rows["Team 1"]["total"] == rows["Team 2"]["total"] == 20
    assert "hint_cost" not in rows["Team 1"]
    assert [r["name"] for r in leaderboard(event)][:2] == ["Team 1", "Team 2"]


def test_speed_is_not_a_hidden_tiebreak(app, event, facilitator):
    """It is worth five points and no more. It does not get a second vote."""
    from app.scoring import leaderboard
    one, two = team_of(event, "Team 1"), team_of(event, "Team 2")
    db.session.add_all([
        ScoreEvent(session_id=event.id, team_id=one.id, source="auto",
                   category="evidence", points=20, actor="server"),
        ScoreEvent(session_id=event.id, team_id=two.id, source="auto",
                   category="evidence", points=20, actor="server"),
        ScoreEvent(session_id=event.id, team_id=two.id, source="speed",
                   category="speed", points=0, actor="server"),
    ])
    db.session.commit()
    assert [r["name"] for r in leaderboard(event)][:2] == ["Team 1", "Team 2"]


def test_a_team_names_itself_before_the_first_mission(app, event):
    p = join(app, event, "Team 1", "kenji")
    res = p.post("/api/team-name", {"name": "Team Kitsune"})
    assert res.status_code == 200
    assert res.get_json()["name"] == "Team Kitsune"
    assert team_of(event, "Team Kitsune").id


def test_the_name_is_normalised_and_stripped_of_control_characters(app):
    assert normalise_team_name("  Team   Kitsune  ") == ("Team Kitsune", None)
    assert normalise_team_name("Ｔｅａｍ　Ｔｏｒａ")[0] == "Team Tora"
    name, error = normalise_team_name("Team‮Kitsune")
    assert error is None and "‮" not in name


def test_a_name_that_is_too_short_or_too_long_is_refused(app, event):
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/team-name", {"name": "K"}).get_json()["error"] == "too_short"
    assert p.post("/api/team-name", {"name": "   "}).get_json()["error"] == "too_short"
    assert p.post("/api/team-name", {"name": "K" * 41}).get_json()["error"] == "too_long"
    assert team_of(event, "Team 1").display_name == "Team 1"


def test_two_teams_cannot_hold_the_same_name(app, event):
    one = join(app, event, "Team 1", "a")
    two = join(app, event, "Team 2", "b")
    assert one.post("/api/team-name", {"name": "Team Tora"}).status_code == 200
    clash = two.post("/api/team-name", {"name": "team tora"})    # case-insensitive
    assert clash.status_code == 400
    assert clash.get_json()["error"] == "taken"


def test_renaming_to_your_own_current_name_is_allowed(app, event):
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/team-name", {"name": "Team Tanuki"})
    assert p.post("/api/team-name", {"name": "Team Tanuki"}).status_code == 200


def test_names_lock_when_the_first_mission_opens(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert names_locked(event) is False
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    assert names_locked(event) is True

    res = p.post("/api/team-name", {"name": "Too Late"})
    assert res.status_code == 409 and res.get_json()["error"] == "locked"
    assert team_of(event, "Team 1").display_name == "Team 1"


def test_names_stay_locked_after_a_mission_closes(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert p.post("/api/team-name", {"name": "Nope"}).status_code == 409


def test_a_team_name_cannot_carry_script_to_another_screen(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/team-name", {"name": "<script>window.__x=1</script>"})
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})

    for url in ("/lobby", "/team", "/scoreboard"):
        assert "<script>window.__x" not in p.get(url).data.decode("utf-8"), url
    assert "<script>window.__x" not in facilitator.get("/facilitator/").data.decode("utf-8")

    row = next(r for r in p.get("/api/scoreboard").get_json()["rows"]
               if r["name"].startswith("<script>"))
    assert row["name"] == "<script>window.__x=1</script>"


def test_the_rename_is_audited(app, event):
    from app.models import AuditEvent
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/team-name", {"name": "Team Kitsune"})
    row = AuditEvent.query.filter_by(action="team_renamed").one()
    assert "Team 1" in row.detail and "Team Kitsune" in row.detail


def test_the_chosen_name_reaches_the_export(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/team-name", {"name": "Team Kitsune"})
    team = team_of(event, "Team Kitsune")
    db.session.add(ScoreEvent(session_id=event.id, team_id=team.id, source="auto",
                              category="correctness", points=10, actor="server"))
    db.session.commit()
    csv_body = facilitator.get("/facilitator/export/results.csv").data.decode("utf-8-sig")
    assert "Team Kitsune" in csv_body
