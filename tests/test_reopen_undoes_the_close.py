from app.models import (
    SRC_AUTO,
    SRC_OVERRIDE,
    SRC_RUBRIC,
    ScoreEvent,
    TeamMission,
    db,
)
from app.scoring import team_total
from app.state import close_mission, mission_row, open_mission

from .conftest import join, team_of, wind_forward

SLUG = "digital-footprint"


def _auto(event, team):
    return ScoreEvent.query.filter(
        ScoreEvent.session_id == event.id,
        ScoreEvent.team_id == team.id,
        ScoreEvent.source == SRC_AUTO).all()


def _stamp(team):
    tm = TeamMission.query.filter_by(
        team_id=team.id, mission_slug=SLUG).one_or_none()
    return tm.submitted_at if tm else None


def test_reopening_reverses_the_automatic_scoring(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)
    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    with app.app_context():
        team = team_of(event, "Team 1")
        row = mission_row(event, SLUG)
        close_mission(event, row)
        assert team_total(team) > 0, "the close should have scored something"
        assert _auto(event, team), "the close should have written auto events"

        open_mission(event, row)

        assert team_total(team) == 0
        assert _auto(event, team), "the ledger is append-only; nothing is deleted"
        reversals = ScoreEvent.query.filter_by(
            session_id=event.id, team_id=team.id, source=SRC_OVERRIDE).all()
        assert reversals and all(r.points < 0 for r in reversals)
        assert all("reopened" in (r.reason or "") for r in reversals)


def test_reopening_unlocks_only_the_teams_the_close_stamped(app, event,
                                                            facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    early = join(app, event, "Team 1", "kenji")
    join(app, event, "Team 2", "aoi")
    wind_forward(app, event, SLUG)
    early.post("/api/submission", {"mission_slug": SLUG,
                                   "question_key": "m1_key_id",
                                   "answer": "SR-REL-2019"})
    early.post("/api/lock", {"mission_slug": SLUG})

    with app.app_context():
        row = mission_row(event, SLUG)
        close_mission(event, row)
        open_mission(event, row)

        assert _stamp(team_of(event, "Team 1")) is not None
        assert _stamp(team_of(event, "Team 2")) is None


def test_a_facilitators_own_marks_survive_a_reopen(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)
    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    with app.app_context():
        team = team_of(event, "Team 1")
        row = mission_row(event, SLUG)
        close_mission(event, row)
        db.session.add(ScoreEvent(session_id=event.id, team_id=team.id,
                                  mission_slug=SLUG, source=SRC_RUBRIC,
                                  category="reasoning", points=7.0,
                                  reason="rubric m1_r_reasoning = 7/10",
                                  actor="facilitator"))
        db.session.commit()

        open_mission(event, row)

        assert team_total(team) == 7.0, (
            "the reopen reverses what the server computed, never what a person "
            "decided")


def test_closing_again_scores_the_state_the_mission_ends_in(app, event,
                                                            facilitator):
    """The failure this whole file exists for: the second close has to score."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    wind_forward(app, event, SLUG)

    with app.app_context():
        close_mission(event, mission_row(event, SLUG))   # the slip
        assert team_total(team_of(event, "Team 1")) == 0
        open_mission(event, mission_row(event, SLUG))

    p.post("/api/submission", {"mission_slug": SLUG,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    with app.app_context():
        close_mission(event, mission_row(event, SLUG))
        assert team_total(team_of(event, "Team 1")) > 0
