"""Facilitator access, the collective board, the synthesis, export, health, XSS."""
import json

from app import missions as content
from app.models import CollectiveIOC, FinalReport, ScoreEvent, Team, db

from .conftest import code_of, join

SLUG = "digital-footprint"


def test_facilitator_routes_refuse_a_participant(app, event):
    p = join(app, event, "Team 1", "kenji")
    assert p.post(f"/facilitator/mission/{SLUG}/open", {}).status_code == 403
    assert p.post("/facilitator/session/reset", {}).status_code == 403
    assert p.get("/facilitator/export/results.csv").status_code in (302, 303)
    assert p.get("/facilitator/api/progress").status_code in (302, 303)


def test_facilitator_routes_refuse_an_anonymous_browser(app, event):
    client = app.test_client()
    assert client.get("/facilitator/").status_code in (302, 303)
    client.get("/")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    assert client.post("/facilitator/session/reset",
                       json={"csrf_token": token}).status_code == 403
    assert client.post("/facilitator/session/reset").status_code == 400   # CSRF layer


def test_wrong_password_does_not_sign_in(app, event):
    client = app.test_client()
    client.get("/facilitator/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    res = client.post("/facilitator/login", data={"csrf_token": token, "password": "guess"})
    assert res.status_code == 401
    assert client.get("/facilitator/").status_code in (302, 303)


def test_empty_password_hash_disables_access_rather_than_opening_it(app, event):
    app.config["FACILITATOR_PASSWORD_HASH"] = ""
    client = app.test_client()
    client.get("/facilitator/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    for attempt in ("", "anything"):
        res = client.post("/facilitator/login", data={"csrf_token": token, "password": attempt})
        assert res.status_code == 401


def test_signing_in_as_facilitator_drops_a_participant_seat(app, event):
    p = join(app, event, "Team 1", "rehearsal")
    from werkzeug.security import generate_password_hash
    app.config["FACILITATOR_PASSWORD_HASH"] = generate_password_hash("pw")
    p.client.post("/facilitator/login", data={"csrf_token": p.csrf, "password": "pw"})
    assert p.get("/api/observations").status_code == 401


def test_a_write_without_the_token_is_refused(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    bare = p.client.post("/api/observation",
                         json={"mission_slug": SLUG, "text": "no token"})
    assert bare.status_code == 400
    assert bare.get_json()["error"] == "csrf"


def test_a_write_with_a_forged_token_is_refused(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    res = p.client.post("/api/observation",
                        json={"mission_slug": SLUG, "text": "x", "csrf_token": "guessed"})
    assert res.status_code == 400


def test_participant_text_is_escaped_everywhere_it_is_rendered(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    payload = '<script>window.__pwned=1</script>'
    p = join(app, event, "Team 1", payload)
    p.post("/api/observation", {"mission_slug": SLUG, "text": payload})
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": payload})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "commit", "excerpt": payload})

    for url in ("/team", "/evidence", f"/mission/{SLUG}", "/collective-intel"):
        body = p.get(url).data.decode("utf-8")
        assert "<script>window.__pwned" not in body, url

    data = p.get("/api/observations").get_json()
    assert data["observations"][0]["text"] == payload

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    body = facilitator.get(f"/facilitator/grade/{team.id}").data.decode("utf-8")
    assert "<script>window.__pwned" not in body


def test_the_page_carries_a_content_security_policy(app, event):
    res = app.test_client().get("/")
    csp = res.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_the_class_board_is_closed_until_the_facilitator_opens_it(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/collective", {"type": "hash", "value": "abc"}).status_code == 409

    facilitator.post("/facilitator/session/state", {"collective_open": True})
    assert p.post("/api/collective", {"type": "hash", "value": "abc"}).status_code == 200


def test_publishing_again_replaces_a_teams_entry(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/collective", {"type": "hash", "value": "first"})
    p.post("/api/collective", {"type": "domain", "value": "second"})
    assert CollectiveIOC.query.count() == 1
    assert CollectiveIOC.query.one().value == "second"


def test_the_shared_bonus_fires_once_when_every_category_is_covered(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    teams = ["Team 1", "Team 2", "Team 3", "Team 4", "Team 5"]
    kinds = ["hash", "domain", "process", "persistence", "file_path"]
    for name, kind in zip(teams, kinds):
        member = join(app, event, name, f"{name}-one")
        member.post("/api/collective", {"type": kind, "value": f"{kind}-value",
                                        "justification": "seen in the report"})

    rows = CollectiveIOC.query.order_by(CollectiveIOC.id).all()
    for i, row in enumerate(rows):
        res = facilitator.post("/facilitator/api/collective/validate",
                               {"ioc_id": row.id, "validated": True}).get_json()
        assert res["bonus_awarded"] is (i == len(rows) - 1)

    bonus = ScoreEvent.query.filter_by(source="collective").all()
    everyone = Team.query.filter_by(session_id=event.id).count()
    assert len(bonus) == everyone >= 5
    assert {b.team_id for b in bonus} == {
        t.id for t in Team.query.filter_by(session_id=event.id).all()}
    assert all(b.points == 15 for b in bonus)

    facilitator.post("/facilitator/api/collective/validate", {"ioc_id": rows[0].id,
                                                              "validated": True})
    assert ScoreEvent.query.filter_by(source="collective").count() == everyone


def test_duplicate_iocs_do_not_widen_coverage(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    for name in ["Team 1", "Team 2", "Team 3"]:
        member = join(app, event, name, f"{name}-one")
        member.post("/api/collective", {"type": "hash", "value": "the same hash"})
    for row in CollectiveIOC.query.all():
        facilitator.post("/facilitator/api/collective/validate",
                         {"ioc_id": row.id, "validated": True})
    assert ScoreEvent.query.filter_by(source="collective").count() == 0


def test_the_synthesis_is_closed_until_the_facilitator_opens_it(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/final", {"verdict": "x"}).status_code == 409
    body = p.get("/final").data.decode("utf-8")
    assert "has not started yet" in body
    assert "after Missions 1 to 3" in body
    assert "Wait for the facilitator" not in body


def test_a_closed_synthesis_is_still_readable(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    for slug in ("digital-footprint", "fake-infrastructure", "threat-intelligence"):
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        facilitator.post(f"/facilitator/mission/{slug}/close", {})
    facilitator.post("/facilitator/session/state", {"final_open": True})
    r = p.post("/api/final", {"verdict": "a compromised token was used"})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]

    facilitator.post("/facilitator/session/state", {"final_open": False})

    body = p.get("/final").data.decode("utf-8")
    assert "Mission 4 is closed" in body
    assert "a compromised token was used" in body, "the team's own words are gone"
    assert "has not started yet" not in body
    assert p.post("/api/final", {"verdict": "changed"}).status_code == 409
    assert "readonly" in body


def test_the_dossier_is_assembled_from_the_three_missions(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_update_url",
                               "answer": "https://update.sakura-vpn-update.com/api/channel.json"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_update_url",
                             "artifact_id": "commit",
                             "excerpt": "UPDATE_CHANNEL_URL = https://update.sakura-vpn-update.com/api/channel.json"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    facilitator.post("/facilitator/session/state", {"final_open": True})

    page = p.get("/final").data.decode("utf-8")
    assert "update.sakura-vpn-update.com/api/channel.json" in page          # the team's own answer, read-only
    assert "m1_update_url" not in page
    assert "which URL does the client use to fetch update information" in page


def test_the_synthesis_cannot_edit_a_closed_mission(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    facilitator.post("/facilitator/session/state", {"final_open": True})
    assert p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_update_url",
                                      "answer": "changed"}).status_code == 409


def test_the_final_report_cannot_be_locked_before_it_opens(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/final/lock", {}).status_code == 409

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    report = FinalReport.query.filter_by(team_id=team.id).one_or_none()
    assert report is None or report.locked_at is None

    facilitator.post("/facilitator/session/state", {"final_open": True})
    assert p.post("/api/final/lock", {}).status_code == 200


def test_the_final_report_saves_and_locks(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/final", {
        "verdict": "A malicious update was distributed through a fake CDN.",
        "timeline": ["e_registration", "e_commit", "e_publish", "e_download",
                     "e_execute", "e_persist", "e_collect", "e_upload"],
        "response": ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"],
        "confirmed_facts": "The package hash matches the sandbox sample.",
        "inferences": "The developer account was probably compromised.",
        "unknowns": "Who was at the keyboard.",
        "limitations": "We never saw the account's authentication logs.",
    })
    assert p.post("/api/final/lock", {}).status_code == 200
    assert p.post("/api/final", {"verdict": "changed"}).status_code == 409

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    report = FinalReport.query.filter_by(team_id=team.id).one()
    assert report.locked_at is not None
    assert json.loads(report.timeline_json)[0] == "e_registration"


def test_auto_scoring_the_synthesis_credits_both_orderings(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/final", {
        "timeline": ["e_registration", "e_commit", "e_publish", "e_download",
                     "e_execute", "e_persist", "e_collect", "e_upload"],
        "response": ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"],
        "response_later": ["f_signing", "f_internal", "f_mfa", "f_review",
                           "f_egress"],
    })
    res = facilitator.post("/facilitator/api/final/score", {}).get_json()
    assert res["events"] >= 3

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    rows = ScoreEvent.query.filter_by(team_id=team.id, mission_slug="final-incident").all()
    assert sum(r.points for r in rows) == 60

    facilitator.post("/facilitator/api/final/score", {})
    rows = ScoreEvent.query.filter_by(team_id=team.id, mission_slug="final-incident").all()
    assert sum(r.points for r in rows) == 60


def test_the_presentation_view_renders_from_the_report(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/final", {"verdict": "A fake update channel delivered a beaconing implant.",
                          "timeline": ["e_registration", "e_commit"],
                          "response": ["a_isolate", "a_preserve"]})
    body = p.get("/presentation").data.decode("utf-8")
    assert "beaconing implant" in body
    assert "Team 1" in body

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    projector = facilitator.get(f"/facilitator/present/{team.id}").data.decode("utf-8")
    assert "projector" in projector


def test_the_scoreboard_returns_no_rows_while_it_is_hidden(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    db.session.add(ScoreEvent(session_id=event.id, team_id=team.id, source="auto",
                              category="correctness", points=42, actor="server"))
    db.session.commit()
    p = join(app, event, "Team 1", "kenji")

    hidden = p.get("/api/scoreboard").get_json()
    assert hidden["visible"] is False and hidden["rows"] == []

    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    shown = p.get("/api/scoreboard").get_json()
    assert shown["visible"] is True
    assert shown["rows"][0]["total"] == 42
    assert shown["provisional"] is True

    from .conftest import finish_all_grading
    finish_all_grading(app, event, facilitator)
    assert facilitator.post("/facilitator/session/state",
                            {"scores_locked": True}).status_code == 200
    final = p.get("/api/scoreboard").get_json()
    assert final["provisional"] is False


def test_no_endpoint_returns_a_per_member_score(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    mine = p.get("/api/my-score").get_json()
    assert "breakdown" in mine
    assert "member" not in json.dumps(mine)
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    for member in state["members"]:
        assert set(member) == {"id", "nickname", "online", "posted", "is_me"}


def test_export_carries_the_whole_ledger(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    facilitator.post("/facilitator/api/override", {
        "team_id": team.id, "category": "other", "points": 7, "reason": "manual award"})

    csv_body = facilitator.get("/facilitator/export/results.csv").data.decode("utf-8-sig")
    assert "manual award" in csv_body
    assert "Team 1" in csv_body

    doc = json.loads(facilitator.get("/facilitator/export/session.json").data.decode("utf-8"))
    assert doc["session"]["code"] == event.code
    assert len(doc["teams"]) == Team.query.filter_by(session_id=event.id).count()
    assert any(a["action"] == "session_created" for a in doc["audit"])


def test_reset_closes_the_session_without_deleting_anything(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/observation", {"mission_slug": SLUG, "text": "kept"})

    code = code_of(event)
    facilitator.post("/facilitator/session/reset", {})
    db.session.refresh(event)
    assert event.state == "closed"

    from app.models import Observation
    assert Observation.query.count() == 1
    client = app.test_client()
    client.get("/join")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    res = client.post("/join", data={"csrf_token": token, "session_code": event.code,
                                     "team_code": code, "nickname": "late"})
    assert res.status_code == 400


def test_health_check_reports_the_shape_of_the_session_not_its_code(app, event):
    """It used to assert the session CODE was published. See test_release_hardening."""
    body = app.test_client().get("/healthz").get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["session_active"] is True
    assert body["teams"] == 6
    assert event.code not in str(body)


def test_the_seed_produces_the_six_teams_the_class_runs(app, event):
    from app.state import DEFAULT_TEAM_COUNT

    assert DEFAULT_TEAM_COUNT == 6
    assert Team.query.filter_by(session_id=event.id).count() == 6
    teams = Team.query.filter_by(session_id=event.id).order_by(Team.id).all()
    assert [t.display_name for t in teams] == [
        "Team 1", "Team 2", "Team 3", "Team 4", "Team 5", "Team 6"]
    colours = [t.color_key for t in teams]
    assert len(set(colours)) == 6, colours


def test_another_team_count_still_works(app):
    """The format is a default, not a belief. Rehearsals use other numbers."""
    from app.state import create_session

    small = create_session(app.config, title="Two-team rehearsal", code="TWOTWO",
                           team_count=2)
    assert Team.query.filter_by(session_id=small.id).count() == 2
    big = create_session(app.config, title="Nine", code="NINEAA", team_count=9)
    names = [t.display_name for t in
             Team.query.filter_by(session_id=big.id).order_by(Team.id).all()]
    assert len(names) == 9 and names[-1] == "Team 9"


def test_every_authored_mission_has_a_row_with_its_content_version(app, event):
    from app.models import Mission
    rows = {r.slug: r for r in Mission.query.filter_by(session_id=event.id).all()}
    for m in content.ordered_missions(app.config["CONTENT_DIR"]):
        assert rows[m["slug"]].content_version == str(m["version"])
