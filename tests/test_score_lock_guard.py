import pytest

from app.models import ScoreEvent
from app.scoring import grading_progress, required_rubric_items

from .conftest import finish_all_grading, join


def progress(app, event):
    return grading_progress(event, app.config["CONTENT_DIR"])


def lock(facilitator):
    return facilitator.post("/facilitator/session/state", {"scores_locked": True})


def grade(facilitator, team, item, points):
    return facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": item["mission_slug"],
        "rubric_key": item["key"], "points": points, "reason": "test"})


def test_an_explicit_zero_is_recorded(app, event, facilitator):
    """It used to write nothing, which made it indistinguishable from silence."""
    item = required_rubric_items(app.config["CONTENT_DIR"])[0]
    team = next(t for t in event.teams if t.display_name == "Team 1")

    before = progress(app, event)
    assert grade(facilitator, team, item, 0).status_code == 200
    after = progress(app, event)

    assert after["done"] == before["done"] + 1
    rows = ScoreEvent.query.filter_by(team_id=team.id,
                                      question_key=item["key"]).all()
    assert len(rows) == 1
    assert rows[0].points == 0.0


def test_a_missing_grade_is_not_a_zero(app, event, facilitator):
    item = required_rubric_items(app.config["CONTENT_DIR"])[0]
    team = next(t for t in event.teams if t.display_name == "Team 1")

    missing = [m["key"] for m in progress(app, event)["per_team"][0]["missing"]]
    assert item["key"] in missing

    grade(facilitator, team, item, 0)
    still_missing = [m["key"] for t in progress(app, event)["per_team"]
                     if t["name"] == "Team 1" for m in t["missing"]]
    assert item["key"] not in still_missing


def test_regrading_to_the_same_value_writes_nothing_new(app, event, facilitator):
    item = required_rubric_items(app.config["CONTENT_DIR"])[0]
    team = next(t for t in event.teams if t.display_name == "Team 1")
    grade(facilitator, team, item, 3)
    grade(facilitator, team, item, 3)
    rows = ScoreEvent.query.filter_by(team_id=team.id,
                                      question_key=item["key"]).all()
    assert len(rows) == 1


def test_locking_is_refused_while_anything_is_missing(app, event, facilitator):
    r = lock(facilitator)
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "grading_incomplete"
    assert body["grading"]["remaining"] > 0
    assert not event.scores_locked


def test_the_refusal_changes_nothing_at_all(app, event, facilitator):
    """No partial application: a call that locks AND announces must do neither."""
    before = event.announcement
    r = facilitator.post("/facilitator/session/state",
                         {"scores_locked": True, "announcement": "should not land",
                          "scoreboard_visible": True})
    assert r.status_code == 409
    assert event.scores_locked is False
    assert event.scoreboard_visible is False
    assert event.announcement == before


def test_one_ungraded_team_is_enough_to_refuse(app, event, facilitator):
    """All six teams, not just the ones that did well."""
    items = required_rubric_items(app.config["CONTENT_DIR"])
    teams = list(event.teams)
    for team in teams[:-1]:
        for item in items:
            grade(facilitator, team, item, 1)

    r = lock(facilitator)
    assert r.status_code == 409
    body = r.get_json()
    assert body["grading"]["incomplete_teams"] == [teams[-1].display_name]
    assert body["grading"]["remaining"] == len(items)


def test_locking_succeeds_once_everything_is_assessed(app, event, facilitator):
    finish_all_grading(app, event, facilitator, points=0)
    p = progress(app, event)
    assert p["complete"] is True
    assert p["remaining"] == 0
    assert lock(facilitator).status_code == 200
    assert event.scores_locked is True


def test_every_team_is_counted(app, event, facilitator):
    from app.state import DEFAULT_TEAM_COUNT

    p = progress(app, event)
    assert p["teams"] == DEFAULT_TEAM_COUNT
    assert p["total"] == p["required_per_team"] * DEFAULT_TEAM_COUNT
    assert len(p["per_team"]) == DEFAULT_TEAM_COUNT


def test_unlocking_is_never_blocked(app, event, facilitator):
    """The guard is on locking. Unlocking is the way back."""
    finish_all_grading(app, event, facilitator)
    lock(facilitator)
    r = facilitator.post("/facilitator/session/state", {"scores_locked": False})
    assert r.status_code == 200
    assert event.scores_locked is False


def test_closing_a_mission_is_never_blocked(app, event, facilitator):
    """Only the global score lock is guarded; the day must still be runnable."""
    assert facilitator.post(
        "/facilitator/mission/digital-footprint/open", {}).status_code == 200
    assert facilitator.post(
        "/facilitator/mission/digital-footprint/close", {}).status_code == 200


@pytest.mark.parametrize("lang,fragment", [
    ("en", "manual assessments are still"),
    ("ja", "手動採点が"),
])
def test_the_refusal_speaks_both_languages(app, event, facilitator, lang,
                                           fragment):
    with facilitator.client.session_transaction() as sess:
        sess["lang"] = lang
    body = lock(facilitator).get_json()
    assert fragment in body["message"], body["message"]


def test_the_refusal_names_the_teams_and_the_count(app, event, facilitator):
    body = lock(facilitator).get_json()
    assert "Team 1" in body["message"]
    assert str(body["grading"]["remaining"]) in body["message"]


def test_the_console_poll_carries_the_progress(app, event, facilitator):
    body = facilitator.get("/facilitator/api/progress").get_json()
    g = body["status"]["grading"]
    from app.state import DEFAULT_TEAM_COUNT
    assert g["teams"] == DEFAULT_TEAM_COUNT
    assert g["complete"] is False
    assert g["per_team"][0]["missing"]


def test_the_console_offers_a_link_to_each_grading_screen(facilitator, event):
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    assert "gradingbar" in html
    assert "'/facilitator/grade/' + t.team_id" in html
    assert "go_to_grading" in html


def test_the_grading_screen_those_links_point_at_exists(app, event, facilitator):
    team = next(t for t in event.teams if t.display_name == "Team 1")
    assert facilitator.get(f"/facilitator/grade/{team.id}").status_code == 200
