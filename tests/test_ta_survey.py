"""The TA questionnaire: saved, validated, exported, and invisible to the room."""
import json

from app.models import AssistantReport, TA_QUESTIONS, Team

from .conftest import join, team_of


def _ta(app, event):
    from werkzeug.security import generate_password_hash
    app.config["ASSISTANT_PASSWORD_HASH"] = generate_password_hash("ta")
    c = app.test_client()
    c.get("/assistant/login")
    with c.session_transaction() as s:
        tok = s["csrf_token"]
    c.post("/assistant/login", data={"csrf_token": tok, "password": "ta"})
    team = team_of(event, "Team 1")
    c.post("/assistant/team", data={"csrf_token": tok, "team_id": team.id})
    return c, tok, team


def test_hint_use_is_a_frequency_with_its_own_words(app, event):
    c, tok, team = _ta(app, event)
    r = c.post("/assistant/api/survey", json={
        "csrf_token": tok, "answers": {"hints": "no_blockage"}})
    assert r.status_code == 200
    saved = json.loads(AssistantReport.query.filter_by(team_id=team.id).one().answers_json)
    assert saved["hints"] == "no_blockage"

    c.post("/assistant/api/survey", json={
        "csrf_token": tok, "answers": {"hints": "4", "participation": "often"}})
    saved = json.loads(AssistantReport.query.filter_by(team_id=team.id).one().answers_json)
    assert saved["hints"] == "no_blockage"          # unchanged, "4" refused
    assert "participation" not in saved             # "often" refused


def test_the_observation_period_is_recorded(app, event):
    c, tok, team = _ta(app, event)
    c.post("/assistant/api/survey", json={
        "csrf_token": tok, "answers": {"task": "4"},
        "period": "Mission 2, 14:10-14:40"})

    body = c.get("/assistant/board").data.decode("utf-8")
    assert "Mission 2, 14:10-14:40" in body
    saved = json.loads(AssistantReport.query.filter_by(team_id=team.id).one().answers_json)
    assert saved["__period"] == "Mission 2, 14:10-14:40"
    assert saved["task"] == "4"


def test_the_form_reads_in_three_named_sections(app, event):
    c, tok, team = _ta(app, event)
    body = c.get("/assistant/board").data.decode("utf-8")

    for heading in ("Teamwork", "Interface and task clarity", "Hint use"):
        assert heading in body, heading
    for word in ("Not at all", "Moderately", "Completely", "Not observed"):
        assert word in body, word
    for word in ("No blockage observed", "Rarely", "Always"):
        assert word in body, word


def test_a_ta_can_record_and_reread_observations(app, event):
    c, tok, team = _ta(app, event)
    r = c.post("/assistant/api/survey", json={
        "csrf_token": tok,
        "answers": {"participation": "2", "discussion": "5", "hints": "na"},
        "notes": "One member drove the whole of Mission 1.",
    })
    assert r.status_code == 200, r.data[:300]
    row = AssistantReport.query.filter_by(team_id=team.id).one()
    saved = json.loads(row.answers_json)
    assert saved == {"participation": "2", "discussion": "5", "hints": "na"}
    assert "drove the whole" in row.notes


def test_rubbish_keys_and_values_are_refused(app, event):
    c, tok, team = _ta(app, event)
    c.post("/assistant/api/survey", json={
        "csrf_token": tok,
        "answers": {"participation": "9", "not_a_question": "3", "task": "4"},
    })
    saved = json.loads(AssistantReport.query.filter_by(team_id=team.id).one().answers_json)
    assert saved == {"task": "4"}          # the 9 and the unknown key are dropped


def test_the_room_never_sees_it(app, event, facilitator):
    c, tok, team = _ta(app, event)
    c.post("/assistant/api/survey", json={"csrf_token": tok,
                                          "answers": {"participation": "1"},
                                          "notes": "TA-ONLY-MARKER"})
    p = join(app, event, "Team 1", "kenji")
    for url in ("/team", "/briefing", "/evidence", "/final", "/scoreboard"):
        assert "TA-ONLY-MARKER" not in p.get(url).data.decode("utf-8"), url
    assert p.post("/assistant/api/survey", {"answers": {}}).status_code in (302, 303, 401, 403)


def test_it_scores_nothing(app, event):
    from app.models import ScoreEvent
    c, tok, team = _ta(app, event)
    c.post("/assistant/api/survey", json={"csrf_token": tok,
                                          "answers": {q: "5" for q in TA_QUESTIONS}})
    assert ScoreEvent.query.count() == 0


def test_the_export_carries_one_row_per_team(app, event, facilitator):
    c, tok, team = _ta(app, event)
    c.post("/assistant/api/survey", json={"csrf_token": tok,
                                          "answers": {"participation": "4"},
                                          "notes": "worked well together"})
    body = facilitator.get("/facilitator/export/ta_reports.csv").data.decode("utf-8-sig")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 1 + Team.query.filter_by(session_id=event.id).count()
    assert all(q in lines[0] for q in TA_QUESTIONS)
    assert "worked well together" in body


def test_the_board_renders_the_questionnaire(app, event):
    c, tok, team = _ta(app, event)
    body = c.get("/assistant/board").data.decode("utf-8")
    assert c.get("/assistant/board").status_code == 200
    assert "tasurvey" in body
    for q in TA_QUESTIONS:
        assert f'data-q="{q}"' in body
    assert 'id="ta-notes"' in body
