"""Validators, the append-only ledger, the speed gate, and the collective bonus."""
from datetime import timedelta

from app import missions as content
from app.models import Mission, ScoreEvent, Team, TeamMission, db, utcnow
from app.scoring import check_answer, leaderboard, team_breakdown

from .conftest import join

SLUG = "digital-footprint"


def q(app, slug, key):
    return content.find_question(content.get_mission(app.config["CONTENT_DIR"], slug), key)


def test_short_text_is_case_and_width_insensitive(app):
    """Japanese keyboards produce full-width Latin without the typist noticing."""
    question = q(app, SLUG, "m1_flag")
    for typed in ["ALLOW_UNSIGNED_RECOVERY", "allow_unsigned_recovery",
                  "  ALLOW_UNSIGNED_RECOVERY  ",
                  "ＡＬＬＯＷ＿ＵＮＳＩ"
                  "ＧＮＥＤ＿ＲＥＣＯＶ"
                  "ＥＲＹ"]:
        assert check_answer(question, typed)[0] is True, typed
    assert check_answer(question, "REQUIRE_SIGNATURE")[0] is False


def test_choice_key_can_live_on_the_options(app):
    question = q(app, SLUG, "m1_attribution")
    assert "validator" not in question
    assert check_answer(question, "compromised_token") == (True, 1.0)
    assert check_answer(question, "sora_did_it")[0] is False


def test_multi_choice_gives_partial_credit_and_penalises_guessing(app):
    question = q(app, "threat-intelligence", "m3_attack")
    full = ["t1059_001", "t1547_001", "t1053_005", "t1071_001", "t1560_001", "t1041"]
    assert check_answer(question, full) == (True, 1.0)

    partial = check_answer(question, full[:3])
    assert partial[0] is False and 0.4 < partial[1] < 0.6

    everything = check_answer(question, full + ["t1195_002", "t1486", "t1110_003"])
    assert everything[1] < 1.0
    assert everything[1] <= partial[1] + 0.51


def test_ordering_gives_pairwise_credit(app):
    question = q(app, "final-incident", "final_timeline")
    key = question["validator"]["accept"]
    assert check_answer(question, key) == (True, 1.0)

    swapped = list(key)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    near = check_answer(question, swapped)
    assert near[0] is False and near[1] > 0.9

    backwards = check_answer(question, list(reversed(key)))
    assert backwards[1] == 0.0


def test_free_text_is_never_auto_scored(app):
    fake = {"type": "free_text", "key": "x"}
    assert check_answer(fake, "anything") == (None, 0.0)


def test_empty_answer_is_wrong_not_an_error(app):
    question = q(app, SLUG, "m1_key_id")
    assert check_answer(question, None) == (False, 0.0)
    assert check_answer(question, "") == (False, 0.0)
    assert check_answer(question, []) == (False, 0.0)


def _answer_everything(p, app, slug, correct=True):
    """Submit the authored key (or a wrong answer) for every auto question."""
    m = content.get_mission(app.config["CONTENT_DIR"], slug)
    for question in m.get("questions") or []:
        if question["type"] not in content.AUTO_TYPES:
            continue
        accept = (question.get("validator") or {}).get("accept")
        if accept is None:
            accept = [o["id"] for o in question.get("options") or [] if o.get("correct")]
        if question["type"] in ("multi_choice", "graph"):
            answer = list(accept)
        elif question["type"] == "order":
            answer = list(accept)
        else:
            answer = accept[0] if isinstance(accept, list) else accept
        if not correct:
            answer = "definitely-wrong" if isinstance(answer, str) else []
        p.post("/api/submission", {"mission_slug": slug, "question_key": question["key"],
                                   "answer": answer})
        if question.get("accepted_evidence"):
            p.post("/api/evidence", {"mission_slug": slug, "question_key": question["key"],
                                     "artifact_id": question["accepted_evidence"][0],
                                     "excerpt": "the relevant line"})


def test_a_perfect_mission_earns_correctness_and_evidence(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _answer_everything(p, app, SLUG)
    p.post("/api/lock", {"mission_slug": SLUG})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"]["correctness"] == 35
    assert breakdown["by_category"]["evidence"] == 30
    assert "reasoning" not in breakdown["by_category"]
    assert "completeness" not in breakdown["by_category"]


def test_wrong_answers_earn_no_correctness_but_real_evidence_still_counts(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _answer_everything(p, app, SLUG, correct=False)
    p.post("/api/lock", {"mission_slug": SLUG})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"].get("correctness") is None
    assert breakdown["by_category"].get("reasoning") is None
    assert breakdown["by_category"]["evidence"] == 30
    assert breakdown["by_category"].get("speed") is None      # correctness gate not met


def test_a_right_answer_with_no_evidence_loses_the_evidence_share(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    m = content.get_mission(app.config["CONTENT_DIR"], SLUG)
    for question in m["questions"]:
        if question["type"] not in content.AUTO_TYPES:
            continue
        accept = (question.get("validator") or {}).get("accept") or \
            [o["id"] for o in question.get("options") or [] if o.get("correct")]
        answer = list(accept) if question["type"] in ("multi_choice", "graph", "order") \
            else accept[0]
        p.post("/api/submission", {"mission_slug": SLUG,
                                   "question_key": question["key"], "answer": answer})
    p.post("/api/lock", {"mission_slug": SLUG})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"]["correctness"] == 35
    assert breakdown["by_category"].get("evidence") is None


def test_speed_needs_seventy_percent_first(app, event, facilitator):
    """A team that locks instantly with nothing right earns no speed points."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    fast_wrong = join(app, event, "Team 1", "fast")
    fast_wrong.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                        "answer": "wrong"})
    fast_wrong.post("/api/lock", {"mission_slug": SLUG})

    right = join(app, event, "Team 2", "careful")
    _answer_everything(right, app, SLUG)
    right.post("/api/lock", {"mission_slug": SLUG})

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    alpha = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    bravo = Team.query.filter_by(session_id=event.id, display_name="Team 2").one()
    assert team_breakdown(alpha)["by_category"].get("speed") is None
    assert team_breakdown(bravo)["by_category"]["speed"] > 0
    assert team_breakdown(bravo)["by_category"]["speed"] <= 5


def test_speed_is_worth_at_most_five_percent(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _answer_everything(p, app, SLUG)
    p.post("/api/lock", {"mission_slug": SLUG})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert team_breakdown(team)["total"] <= 100


def test_speed_reads_when_the_TEAM_locked_not_when_the_mission_closed(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    row = Mission.query.filter_by(session_id=event.id, slug=SLUG).one()

    early = join(app, event, "Team 1", "early")
    _answer_everything(early, app, SLUG)
    early.post("/api/lock", {"mission_slug": SLUG})

    late = join(app, event, "Team 2", "late")
    _answer_everything(late, app, SLUG)
    late.post("/api/lock", {"mission_slug": SLUG})

    late_team = Team.query.filter_by(session_id=event.id, display_name="Team 2").one()
    tm = TeamMission.query.filter_by(team_id=late_team.id, mission_slug=SLUG).one()
    tm.submitted_at = row.opened_at + timedelta(seconds=row.total_seconds - 60)
    db.session.commit()

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    early_team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    fast = team_breakdown(early_team)["by_category"]["speed"]
    slow = team_breakdown(late_team)["by_category"]["speed"]
    assert fast > slow, f"same bonus for both: {fast} vs {slow}"
    assert slow < 0.5, "a minute left out of thirty is nearly no bonus"


def test_closing_early_does_not_hand_the_bonus_to_a_team_that_never_locked(app, event,
                                                                          facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.get(f"/mission/{SLUG}")
    _answer_everything(p, app, SLUG)
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"]["correctness"] == 35
    assert breakdown["by_category"].get("speed") is None


def test_a_team_that_ran_out_of_time_is_still_scored(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _answer_everything(p, app, SLUG)
    row = Mission.query.filter_by(session_id=event.id, slug=SLUG).one()
    row.opened_at = utcnow() - timedelta(seconds=1900)
    db.session.commit()
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"]["correctness"] == 35
    assert breakdown["by_category"].get("speed") is None      # nothing for running out


def test_the_ledger_is_append_only_and_a_regrade_is_a_new_row(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": SLUG,
        "rubric_key": "m1_r_coverage", "points": 6})
    facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": SLUG,
        "rubric_key": "m1_r_coverage", "points": 9})

    rows = ScoreEvent.query.filter_by(team_id=team.id, question_key="m1_r_coverage") \
        .order_by(ScoreEvent.id).all()
    assert [r.source for r in rows] == ["rubric", "override"]
    assert [r.points for r in rows] == [6, 3]
    assert team_breakdown(team)["by_category"]["completeness"] == 9


def test_a_rubric_is_capped_at_its_authored_maximum(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": SLUG,
        "rubric_key": "m1_r_coverage", "points": 500})
    assert team_breakdown(team)["total"] == 10


def test_an_override_without_a_reason_is_refused(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    res = facilitator.post("/facilitator/api/override", {
        "team_id": team.id, "category": "other", "points": 10, "reason": "  "})
    assert res.status_code == 400
    assert ScoreEvent.query.count() == 0


def test_an_override_records_who_and_why(app, event, facilitator):
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    facilitator.post("/facilitator/api/override", {
        "team_id": team.id, "category": "reasoning", "points": -5,
        "reason": "answer copied from the team next to them"})
    row = ScoreEvent.query.filter_by(source="override").one()
    assert row.points == -5
    assert row.actor == "facilitator"
    assert "copied" in row.reason


def test_locked_scores_refuse_further_grading(app, event, facilitator):
    from .conftest import finish_all_grading

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    finish_all_grading(app, event, facilitator)
    assert facilitator.post("/facilitator/session/state",
                            {"scores_locked": True}).status_code == 200
    res = facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": SLUG,
        "rubric_key": "m1_evidence_quality", "points": 10})
    assert res.status_code == 409


def test_ties_break_on_evidence_then_on_hints(app, event, facilitator):
    alpha, bravo = (Team.query.filter_by(session_id=event.id, display_name=c).one()
                    for c in ("Team 1", "Team 2"))
    db.session.add_all([
        ScoreEvent(session_id=event.id, team_id=alpha.id, source="auto",
                   category="correctness", points=30, actor="server"),
        ScoreEvent(session_id=event.id, team_id=alpha.id, source="auto",
                   category="evidence", points=10, actor="server"),
        ScoreEvent(session_id=event.id, team_id=bravo.id, source="auto",
                   category="correctness", points=20, actor="server"),
        ScoreEvent(session_id=event.id, team_id=bravo.id, source="auto",
                   category="evidence", points=20, actor="server"),
    ])
    db.session.commit()
    rows = leaderboard(event)
    assert rows[0]["total"] == rows[1]["total"] == 40
    assert rows[0]["name"] == bravo.display_name       # more evidence wins the tie
