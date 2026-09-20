import json

from app.models import (EventSession, Member, ScoreEvent, Submission, Team,
                        TeamMission, db)
from app.state import DEFAULT_TEAM_COUNT, create_session

from .conftest import join

TEAM_SIZES = (4, 4, 4, 5, 5, 5)
CLASS_SIZE = sum(TEAM_SIZES)
MISSIONS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")


def sizes_for(event):
    """One size per team, padded from TEAM_SIZES if the format ever grows."""
    count = Team.query.filter_by(session_id=event.id).count()
    return [TEAM_SIZES[i] if i < len(TEAM_SIZES) else TEAM_SIZES[-1]
            for i in range(count)]


def team_names(event):
    return [t.display_name for t in
            Team.query.filter_by(session_id=event.id).order_by(Team.id).all()]


def test_a_session_is_created_with_six_teams_and_six_distinct_codes(app, event):
    teams = Team.query.filter_by(session_id=event.id).order_by(Team.id).all()
    assert len(teams) == DEFAULT_TEAM_COUNT == 6
    codes = [t.code for t in teams]
    assert len(set(codes)) == 6, "the facilitator reads these out; two are the same"
    assert len(set(t.color_key for t in teams)) == 6, "two teams share a row colour"
    assert team_names(event)[-1] == "Team 6"


def test_twenty_seven_participants_across_six_uneven_teams(app, event):
    """4, 4, 4, 5, 5, 5 — and every one of them a distinct person."""
    names = team_names(event)
    sizes = sizes_for(event)
    members = {}
    for name, size in zip(names, sizes):
        for i in range(size):
            join(app, event, name, f"{name.replace(' ', '')}-{i}")
        team = Team.query.filter_by(session_id=event.id, display_name=name).one()
        members[name] = [m.nickname for m in team.members.all()]

    assert sum(len(v) for v in members.values()) == CLASS_SIZE == 27
    for (name, nicknames), size in zip(members.items(), sizes):
        assert len(nicknames) == size, (name, nicknames)
        assert len(set(nicknames)) == size, (name, nicknames)
    assert Member.query.count() == 27
    assert sorted(sizes) == [4, 4, 4, 5, 5, 5]


def test_the_smaller_teams_are_not_treated_differently(app, event, facilitator):
    """A five is a whole team. Nothing anywhere asks how many people it has."""
    slug = MISSIONS[0]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    names = team_names(event)
    sizes = sizes_for(event)

    firsts = {}
    for name, size in zip(names, sizes):
        seats = [join(app, event, name, f"{name.replace(' ', '')}-{i}")
                 for i in range(size)]
        firsts[name] = seats[0]

    for name, p in firsts.items():
        assert p.get(f"/mission/{slug}").status_code == 200, name
        assert p.post("/api/submission",
                      {"mission_slug": slug, "question_key": "m1_key_id",
                       "answer": "SR-REL-2019"}).get_json()["ok"], name
        assert p.post("/api/evidence",
                      {"mission_slug": slug, "question_key": "m1_key_id",
                       "artifact_id": "commit",
                       "excerpt": "same excerpt"}).get_json()["ok"], name
        assert p.post("/api/lock", {"mission_slug": slug}).get_json()["ok"], name

    facilitator.post(f"/facilitator/mission/{slug}/close", {})
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})

    breakdowns = {name: p.get("/api/my-score").get_json()["breakdown"]
                  for name, p in firsts.items()}
    reference = breakdowns[names[0]]
    for name, got in breakdowns.items():
        assert got == reference, (name, got, reference)


def test_answers_stay_inside_their_own_team_across_all_of_them(app, event, facilitator):
    """Twenty-seven people on one board, five walls between them."""
    slug = MISSIONS[0]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})

    writers = {}
    for name in team_names(event):
        p = join(app, event, name, f"{name.replace(' ', '')}-writer")
        p.post("/api/submission", {"mission_slug": slug, "question_key": "m1_key_id",
                                   "answer": f"answer-from-{name}"})
        writers[name] = p

    for name, p in writers.items():
        seen = p.get(f"/api/mission-state?mission_slug={slug}").get_json()
        assert seen["answers"]["m1_key_id"]["answer"] == f"answer-from-{name}"

    rows = Submission.query.filter_by(mission_slug=slug, question_key="m1_key_id").all()
    assert len(rows) == DEFAULT_TEAM_COUNT
    assert len({r.team_id for r in rows}) == DEFAULT_TEAM_COUNT


def test_the_whole_day_runs_at_the_full_team_count(app, event, facilitator):
    """Open and close three missions, synthesis, grading, board, exports."""
    teams = team_names(event)
    people = {name: join(app, event, name, f"{name.replace(' ', '')}-lead")
              for name in teams}

    for slug in MISSIONS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        for name, p in people.items():
            p.post("/api/observation", {"mission_slug": slug,
                                        "text": f"{name} looked at {slug}"})
            if slug == MISSIONS[0]:
                p.post("/api/submission", {"mission_slug": slug,
                                           "question_key": "m1_key_id",
                                           "answer": "SR-REL-2019"})
                p.post("/api/lock", {"mission_slug": slug})
        facilitator.post(f"/facilitator/mission/{slug}/close", {})

    from app.models import Mission
    states = {m.slug: m.state for m in Mission.query.filter_by(session_id=event.id).all()}
    for slug in MISSIONS:
        assert states[slug] == "closed", states

    scored = ScoreEvent.query.filter_by(session_id=event.id,
                                        mission_slug=MISSIONS[0]).all()
    assert scored, "closing a mission with answers scored nothing"
    assert len({e.team_id for e in scored}) == DEFAULT_TEAM_COUNT, \
        sorted({e.team_id for e in scored})
    assert TeamMission.query.filter_by(mission_slug=MISSIONS[0]).filter(
        TeamMission.submitted_at.isnot(None)).count() == DEFAULT_TEAM_COUNT

    facilitator.post("/facilitator/session/state", {"collective_open": True})
    for name, kind in zip(teams, ["hash", "domain", "process", "persistence",
                                  "file_path", "ip"]):
        people[name].post("/api/collective", {"type": kind, "value": f"{kind}-{name}",
                                              "justification": "from the report"})
    from app.models import CollectiveIOC
    published = CollectiveIOC.query.filter_by(session_id=event.id).all()
    assert len(published) == DEFAULT_TEAM_COUNT, [(i.team_id, i.ioc_type) for i in published]
    assert len({i.team_id for i in published}) == DEFAULT_TEAM_COUNT

    facilitator.post("/facilitator/session/state", {"final_open": True})
    for name, p in people.items():
        p.post("/api/final", {
            "verdict": f"{name} concludes a fake update channel delivered an implant.",
            "timeline": ["e_registration", "e_commit", "e_publish"],
            "response": ["a_isolate", "a_preserve"],
        })
        assert p.post("/api/final/lock", {}).status_code == 200
    facilitator.post("/facilitator/api/final/score", {})

    from app.models import FinalReport
    assert FinalReport.query.filter_by(session_id=event.id).count() == DEFAULT_TEAM_COUNT

    from .conftest import finish_all_grading

    blocked = facilitator.post("/facilitator/session/state",
                               {"scoreboard_visible": True, "scores_locked": True})
    assert blocked.status_code == 409, "an ungraded session must not lock"
    assert event.scoreboard_visible is False, "the refusal must be whole"

    finish_all_grading(app, event, facilitator, points=1)

    assert facilitator.post(
        "/facilitator/session/state",
        {"scoreboard_visible": True, "scores_locked": True}).status_code == 200
    board = people[teams[0]].get("/api/scoreboard").get_json()
    assert board["visible"] is True
    assert len(board["rows"]) == DEFAULT_TEAM_COUNT, "the reveal must have a line for every team"
    assert {r["name"] for r in board["rows"]} == set(teams), board["rows"]
    ranks = [r["rank"] for r in board["rows"]]
    assert ranks == list(range(1, DEFAULT_TEAM_COUNT + 1)), ranks

    csv_body = facilitator.get("/facilitator/export/results.csv").data.decode("utf-8-sig")
    for name in teams:
        assert name in csv_body, f"{name} is missing from the CSV"

    doc = json.loads(facilitator.get("/facilitator/export/session.json").data.decode("utf-8"))
    assert len(doc["teams"]) == DEFAULT_TEAM_COUNT
    assert {t["name"] for t in doc["teams"]} == set(teams)
    assert all(t["members"] for t in doc["teams"]), doc["teams"]


def test_every_team_survives_a_restart(app, event):
    """The volume outlives the process; so must every team on it."""
    before = {(t.code, t.display_name, t.color_key)
              for t in Team.query.filter_by(session_id=event.id).all()}
    assert len(before) == DEFAULT_TEAM_COUNT

    db.session.expire_all()
    again = EventSession.query.filter_by(code=event.code).one()
    after = {(t.code, t.display_name, t.color_key)
             for t in Team.query.filter_by(session_id=again.id).all()}
    assert after == before


def test_the_engine_is_not_wired_to_six(app):
    small = create_session(app.config, title="Rehearsal", code="SMALL1", team_count=2)
    assert Team.query.filter_by(session_id=small.id).count() == 2

    from app.scoring import collective_coverage
    assert isinstance(collective_coverage(small), dict)
