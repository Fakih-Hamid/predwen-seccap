import json

import pytest

from .conftest import join

SLUG = "digital-footprint"
SIZES = (4, 4, 4, 5, 5, 5)


def progress(facilitator):
    r = facilitator.get("/facilitator/api/progress")
    assert r.status_code == 200
    return r.get_json()


def row_for(payload, name):
    return next(r for r in payload["teams"] if r["name"] == name)


@pytest.fixture
def room(app, event, facilitator):
    """Six uneven teams, joined for real through the form."""
    from app.models import Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    seats = {}
    with app.app_context():
        names = [t.display_name for t in
                 Team.query.filter_by(session_id=event.id).order_by(Team.id)]
    for name, size in zip(names, SIZES):
        seats[name] = [join(app, event, name, f"{name.replace(' ', '')}-{i}")
                       for i in range(size)]
    return names, seats


def test_the_payload_answers_every_question_for_every_team(room, facilitator):
    names, _ = room
    payload = progress(facilitator)
    assert len(payload["teams"]) == len(names)

    for row in payload["teams"]:
        assert row["members"], row["name"]
        assert "online" in row and "size" in row
        assert "last_activity" in row
        assert "hints_taken" in row
        assert "score" in row
        mission = row["missions"][SLUG]
        for field in ("answered", "questions", "evidence", "evidence_wanted",
                      "evidence_missing", "locked", "scored"):
            assert field in mission, (row["name"], field)

    assert "grading" in payload["status"]
    assert "attention" in payload["status"]
    assert "coverage" in payload["collective"]
    assert payload["status"]["elapsed"] is not None, "facilitators keep the clock"


def test_uneven_team_sizes_are_reported_as_they_are(room, facilitator):
    names, _ = room
    payload = progress(facilitator)
    sizes = [row["size"] for row in payload["teams"]]
    assert sizes == list(SIZES)
    assert sorted(sizes) == [4, 4, 4, 5, 5, 5]
    for row in payload["teams"]:
        assert len(row["members"]) == row["size"]


def test_missing_evidence_names_the_questions_not_a_count(room, facilitator):
    names, seats = room
    p = seats[names[0]][0]
    payload = progress(facilitator)
    before = row_for(payload, names[0])["missions"][SLUG]
    assert before["evidence_missing"] == sorted(before["evidence_missing"])
    assert before["evidence_wanted"] == len(before["evidence_missing"])
    assert "m1_key_id" in before["evidence_missing"]

    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "commit", "excerpt": "here"})

    after = row_for(progress(facilitator), names[0])["missions"][SLUG]
    assert "m1_key_id" not in after["evidence_missing"]
    assert len(after["evidence_missing"]) == len(before["evidence_missing"]) - 1


def test_last_activity_moves_when_a_team_writes(room, facilitator):
    """Presence says a browser is polling. This says work happened."""
    names, seats = room
    assert row_for(progress(facilitator), names[0])["last_activity"] is None

    seats[names[0]][0].post("/api/observation",
                            {"mission_slug": SLUG, "text": "the updater"})
    first = row_for(progress(facilitator), names[0])["last_activity"]
    assert first is not None

    seats[names[0]][1].post("/api/submission",
                            {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "answer": "SR-REL-2019"})
    assert row_for(progress(facilitator), names[0])["last_activity"] >= first


def test_hints_and_locks_are_visible_per_team(room, facilitator):
    names, seats = room
    p = seats[names[1]][0]
    p.post("/api/hint/next", {"mission_slug": SLUG})
    p.post("/api/lock", {"mission_slug": SLUG})

    row = row_for(progress(facilitator), names[1])
    assert row["hints_taken"] or row["missions"][SLUG]["locked"]
    assert row["missions"][SLUG]["locked"] is True
    other = row_for(progress(facilitator), names[2])
    assert other["missions"][SLUG]["locked"] is False


def test_a_team_nobody_is_in_is_flagged(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    flagged = progress(facilitator)["status"]["attention"]
    assert flagged, "six empty teams and nothing said"
    assert all(a["why"] == "offline" for a in flagged), flagged


def test_the_idle_rule_is_wired_and_reads_last_activity(app):
    from app.facilitator.routes import _idle_for
    from app.models import utcnow
    from datetime import timedelta

    assert _idle_for(None) == 0, "never written is a different flag"
    recent = (utcnow() - timedelta(seconds=60)).isoformat()
    assert 0 < _idle_for(recent) < 600
    stale = (utcnow() - timedelta(seconds=900)).isoformat()
    assert _idle_for(stale) > 600


def test_the_console_names_the_idle_reason_in_both_languages(app):
    from app import ui

    for lang in ("en", "ja"):
        text = ui.t(app.config["CONTENT_DIR"], "facilitator.st_idle", lang)
        assert text and not text.startswith("⟦"), lang


def test_the_console_polls_rather_than_waiting_to_be_reloaded(app, event,
                                                              facilitator):
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    assert "/facilitator/api/progress" in html
    assert "poll(" in html


def test_the_payload_is_json_and_carries_no_answer_key(room, facilitator):
    blob = json.dumps(progress(facilitator))
    for answer in ("SR-REL-2019", "ALLOW_UNSIGNED_RECOVERY",
                   "ff749df77076e1243eb3c8a3b8bb049d2e7913125532b6ab2bf0b8299ef8ea31"):
        assert answer not in blob, answer
