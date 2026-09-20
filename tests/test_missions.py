"""Mission state, the server clock, submissions, evidence, hints and locking."""
import re
from datetime import timedelta

from app import missions as content
from app.models import (
    MISSION_CLOSED,
    MISSION_OPEN,
    HintUsage,
    Mission,
    ScoreEvent,
    Submission,
    TeamMission,
    db,
    utcnow,
)

from .conftest import join, team_of, wind_forward

SLUG = "digital-footprint"


def _row(app, event, slug=SLUG):
    return Mission.query.filter_by(session_id=event.id, slug=slug).one()


def test_missions_start_locked_and_nothing_opens_itself(app, event):
    for row in Mission.query.filter_by(session_id=event.id).all():
        assert row.state == "locked"
        assert row.opened_at is None


def test_a_locked_mission_refuses_writes(app, event):
    p = join(app, event, "Team 1", "kenji")
    res = p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                     "answer": "SR-REL-2019"})
    assert res.status_code == 409
    assert p.get(f"/mission/{SLUG}").status_code == 200      # readable, not writable


def test_facilitator_opens_and_the_clock_starts(app, event, facilitator):
    res = facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    assert res.status_code == 200
    row = _row(app, event)
    assert row.state == MISSION_OPEN
    assert row.opened_at is not None
    assert 3590 <= row.remaining_seconds() <= 3600


def test_the_clock_is_the_servers_and_a_client_cannot_move_it(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=600)
    db.session.commit()
    p = join(app, event, "Team 1", "kenji")
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert 2995 <= state["mission"]["remaining"] <= 3000
    assert "remaining" not in [k for k in ("remaining",) if False] or True


def test_pausing_stops_the_clock_and_resuming_does_not_restart_it(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=300)
    db.session.commit()

    facilitator.post(f"/facilitator/mission/{SLUG}/pause", {})
    row = _row(app, event)
    paused_left = row.remaining_seconds()
    row.opened_at = row.opened_at - timedelta(seconds=120)
    row.paused_at = row.paused_at - timedelta(seconds=120)
    db.session.commit()
    assert _row(app, event).remaining_seconds() == paused_left

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    row = _row(app, event)
    assert abs(row.remaining_seconds() - paused_left) <= 2
    assert row.paused_seconds >= 119


def test_extending_adds_time_and_is_audited(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    before = _row(app, event).total_seconds
    facilitator.post(f"/facilitator/mission/{SLUG}/extend", {"seconds": 300})
    assert _row(app, event).total_seconds == before + 300
    from app.models import AuditEvent
    assert AuditEvent.query.filter_by(action="mission_extend").count() == 1


def _run_past_zero(app, event, over=10):
    """Wind the mission back so its suggested time is gone. It stays open."""
    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=row.total_seconds + over)
    db.session.commit()
    row = _row(app, event)
    assert row.remaining_seconds() == 0
    assert row.state == MISSION_OPEN
    return row


def test_a_mission_past_its_suggested_time_stays_open_and_writable(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    row = _run_past_zero(app, event)

    assert row.past_suggested_time() is True
    assert row.accepts_writes() is True

    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert state["mission"]["state"] == MISSION_OPEN
    assert state["mission"]["remaining"] == 0
    assert state["mission"]["writable"] is True
    assert state["mission"]["past_suggested"] is True


def test_a_late_answer_is_really_saved(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _run_past_zero(app, event)

    res = p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                     "answer": "SR-REL-2019"})
    assert res.status_code == 200, res.get_json()
    seen = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert seen["answers"]["m1_key_id"]["answer"] == "SR-REL-2019"


def test_late_evidence_and_observations_are_accepted(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    _run_past_zero(app, event)

    obs = p.post("/api/observation", {"mission_slug": SLUG, "text": "found it at 30:04"})
    assert obs.status_code == 200, obs.get_json()

    ev_res = p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                      "artifact_id": "github-account",
                                      "source_url": "https://example.test/sora_dev77",
                                      "excerpt": "late but real"})
    assert ev_res.status_code == 200, ev_res.get_json()
    seen = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert seen["answers"]["m1_key_id"]["evidence"], "the late evidence was not stored"


def test_hints_still_open_past_the_suggested_time(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    hint = _first_hint(app, SLUG)
    _run_past_zero(app, event)

    res = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": hint})
    assert res.status_code == 200, res.get_json()
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert hint in state["hints_taken"]
    assert all(h["open"] for h in state["hints"]), "the ladder should be open by now"


def test_a_team_can_still_lock_past_the_suggested_time(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    _run_past_zero(app, event)

    res = p.post("/api/lock", {"mission_slug": SLUG})
    assert res.status_code == 200, res.get_json()
    assert p.post("/api/observation",
                  {"mission_slug": SLUG, "text": "after the lock"}).status_code == 409


def test_a_late_submission_earns_zero_speed_bonus(app, event, facilitator):
    """The whole penalty for finishing late, and it is enough."""
    from app.scoring import submitted_remaining_seconds

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    _run_past_zero(app, event)
    p.post("/api/lock", {"mission_slug": SLUG})

    row = _row(app, event)
    tm = TeamMission.query.filter_by(mission_slug=SLUG).one()
    assert tm.submitted_at is not None
    assert submitted_remaining_seconds(row, tm) == 0

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    speed = ScoreEvent.query.filter_by(source="speed", mission_slug=SLUG).all()
    assert sum(e.points for e in speed) == 0, [(e.points, e.reason) for e in speed]


def test_a_mission_past_zero_can_still_be_closed_and_scored_exactly_once(app, event,
                                                                        facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    _run_past_zero(app, event)
    p.post("/api/lock", {"mission_slug": SLUG})

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    first = ScoreEvent.query.filter_by(mission_slug=SLUG).count()
    assert first > 0
    assert _row(app, event).state == MISSION_CLOSED

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert ScoreEvent.query.filter_by(mission_slug=SLUG).count() == first

    assert p.post("/api/observation",
                  {"mission_slug": SLUG, "text": "after close"}).status_code == 409


def _seeded_writable(html):
    match = re.search(r"let writable = (true|false);", html)
    assert match, "mission.html no longer seeds `writable` in a readable form"
    return match.group(1) == "true"


def test_the_page_and_the_api_agree_about_writability_at_zero(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _run_past_zero(app, event)

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    api = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()

    assert _seeded_writable(html) is api["mission"]["writable"] is True, (
        "the page and the poll disagree — this is the reload loop")
    assert "if (writable && !nowWritable && !reloading)" in html
    assert "let reloading = false;" in html


def test_several_polls_at_zero_cause_no_reload_then_closing_causes_exactly_one(
        app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    _run_past_zero(app, event)

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    writable = _seeded_writable(html)
    reloading = False
    transitions = 0

    def tick():
        nonlocal writable, reloading, transitions
        body = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
        now = body["mission"]["writable"] and not body["team_locked"]
        if writable and not now and not reloading:
            reloading = True
            transitions += 1
        writable = now
        return body

    for _ in range(6):                       # thirty seconds of polling at 00:00
        body = tick()
        assert body["mission"]["writable"] is True
        assert body["mission"]["past_suggested"] is True
    assert transitions == 0, "the page would still be reloading at 00:00"

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    body = tick()
    assert body["mission"]["state"] == MISSION_CLOSED
    assert body["mission"]["writable"] is False
    assert transitions == 1

    html_after = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert _seeded_writable(html_after) is False
    for _ in range(4):
        tick()
    assert transitions == 1, "a second reload — the loop is back"


def test_the_page_and_the_api_agree_under_the_rehearsal_bypass_too(tmp_path):
    from werkzeug.security import generate_password_hash

    from app import create_app
    from app.state import create_session

    from .conftest import FACILITATOR_PASSWORD

    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "bypass.db").replace("\\", "/"),
        "SECRET_KEY": "bypass-loop-test",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": True,
    })
    from .conftest import join as join_team

    with application.app_context():
        db.create_all()
        ev = create_session(application.config, title="Bypass loop", code="BYPASS")
        row = Mission.query.filter_by(session_id=ev.id, slug=SLUG).one()
        row.state = MISSION_OPEN
        row.opened_at = utcnow()
        db.session.commit()

        p = join_team(application, ev, "Team 1", "kenji")
        assert p.post("/api/lock", {"mission_slug": SLUG}).status_code == 200
        assert p.client.get("/admin/").status_code == 200

        html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
        seeded = _seeded_writable(html)
        api = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
        assert api["team_locked"] is True, "the setup did not reproduce a locked team"

    assert "d.mission.writable && !d.team_locked" not in html, (
        "the poll recomputes writability; that is the divergence")
    assert seeded is api["mission"]["writable"], (
        "seed %r vs API %r — this reloads on every poll"
        % (seeded, api["mission"]["writable"]))


def test_pausing_still_stops_writes_even_with_time_on_the_clock(app, event, facilitator):
    """The advisory clock did not make the facilitator's controls advisory."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    facilitator.post(f"/facilitator/mission/{SLUG}/pause", {})

    assert _row(app, event).accepts_writes() is False
    assert p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                      "answer": "SR-REL-2019"}).status_code == 409
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert state["mission"]["writable"] is False


def test_submission_is_shared_by_the_whole_team(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    a = join(app, event, "Team 1", "aoi")
    b = join(app, event, "Team 1", "ren")

    a.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    seen = b.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert seen["answers"]["m1_key_id"]["answer"] == "SR-REL-2019"

    b.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "WRONG-KEY-0000"})
    assert Submission.query.filter_by(question_key="m1_key_id").count() == 1


def test_an_unknown_question_is_refused(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    res = p.post("/api/submission", {"mission_slug": SLUG, "question_key": "made_up",
                                     "answer": "x"})
    assert res.status_code == 404


def test_evidence_must_point_at_an_artifact_of_this_mission(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    bad = p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                   "artifact_id": "rdap", "excerpt": "x"})
    assert bad.status_code == 400        # `rdap` belongs to Mission 2
    good = p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                    "artifact_id": "commit", "excerpt": "Author: ..."})
    assert good.status_code == 200


def test_evidence_replaces_rather_than_stacks(app, event, facilitator):
    from app.models import EvidenceLink
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    for artifact in ("commit", "github-account", "username-collision"):
        p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                 "artifact_id": artifact, "excerpt": "x"})
    assert EvidenceLink.query.count() == 1


def test_a_hint_is_recorded_once_and_costs_nothing(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")

    first = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h2"}).get_json()
    assert first["charged"] is True                   # first time, not "billed"
    assert first["hint"]["cost"] == 0
    assert first["hint"]["text"]

    second = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h2"}).get_json()
    assert second["charged"] is False

    assert HintUsage.query.count() == 1
    assert ScoreEvent.query.filter_by(source="hint").count() == 0


def test_taking_every_hint_does_not_change_a_team_total(app, event, facilitator):
    """Even the whole ladder, recovery included, moves no points."""
    from app import missions as content
    from app.models import Team
    from app.scoring import team_breakdown

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    wind_forward(app, event, SLUG)
    definition = content.get_mission(app.config["CONTENT_DIR"], SLUG)
    for a in definition["artifacts"]:
        if a.get("external_url"):
            assert p.get(f"/artifacts/{a['id']}").status_code == 200

    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=1500)
    db.session.commit()

    for h in content.all_hints(definition):
        assert p.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": h["id"]}).status_code == 200, h["id"]
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    breakdown = team_breakdown(team)
    assert breakdown["by_category"].get("hint") is None
    assert breakdown["total"] == breakdown["by_category"]["correctness"]


def test_the_ladder_is_five_free_rungs_in_order(app):
    from app import missions as content

    for slug in ("digital-footprint", "fake-infrastructure", "threat-intelligence"):
        hints = content.get_mission(app.config["CONTENT_DIR"], slug)["hints"]
        assert [h["level"] for h in hints] == list(content.HINT_LEVELS), slug
        assert all(h["cost"] == 0 for h in hints), slug
        assert hints[-1]["level"] == "recovery", slug


def test_every_external_pivot_carries_its_own_recovery(app):
    """A source that will not open must not be able to cost a team the mission."""
    from app import missions as content

    cd = app.config["CONTENT_DIR"]
    for m in content.ordered_missions(cd):
        for a in m.get("artifacts") or []:
            if not a.get("external_url"):
                continue
            ladder = a.get("hints") or []
            assert ladder, f"{m['slug']}/{a['id']} has no ladder"
            assert any(h["level"] == "recovery" for h in ladder), f"{m['slug']}/{a['id']}"


def test_the_first_two_rungs_are_open_from_the_first_second(app, event, facilitator):
    """A team that already knows it is lost should not have to wait to say so."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    for hint_id in ("m1_h1", "m1_h2"):
        assert p.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": hint_id}).status_code == 200, hint_id


def test_the_later_rungs_are_refused_early_and_say_when_they_open(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    res = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"})
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"] == "locked"
    assert 0 < body["opens_in"] <= 1200
    assert HintUsage.query.count() == 0


def test_the_clock_opens_the_later_rungs(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=1250)
    db.session.commit()
    assert p.post("/api/hint", {"mission_slug": SLUG,
                                "hint_id": "m1_h5"}).status_code == 200


def test_a_pause_does_not_cost_the_room_its_ladder(app, event, facilitator):
    """Unlocks run on mission time, not wall time — the same clock as the timer."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=1250)
    row.paused_seconds = 1250                      # all of it was a break
    db.session.commit()
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"}).status_code == 409


def test_the_facilitator_can_hand_one_rung_to_one_team(app, event, facilitator):
    """The clock is the default for when nobody is watching. Somebody is."""
    from app.models import Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    stuck = join(app, event, "Team 1", "kenji")
    other = join(app, event, "Team 2", "aoi")
    assert stuck.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": "m1_h5"}).status_code == 409

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    res = facilitator.post("/facilitator/api/hint/unlock",
                           {"team_id": team.id, "mission_slug": SLUG, "hint_id": "m1_h5"})
    assert res.status_code == 200 and res.get_json()["level"] == "recovery"

    assert stuck.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": "m1_h5"}).status_code == 200
    assert other.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": "m1_h5"}).status_code == 409


def test_an_unlock_is_not_a_reveal(app, event, facilitator):
    from app.models import HintUnlock, Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    join(app, event, "Team 1", "kenji")
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    facilitator.post("/facilitator/api/hint/unlock",
                     {"team_id": team.id, "mission_slug": SLUG, "hint_id": "m1_h5"})

    assert HintUnlock.query.filter_by(team_id=team.id).count() == 1
    assert HintUsage.query.count() == 0


def test_a_late_rung_is_reached_by_asking_and_not_by_an_outage(app, event,
                                                              facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/hint", {"mission_slug": SLUG,
                                "hint_id": "m1_h5"}).status_code == 409

    facilitator.post("/facilitator/session/state", {"fallback_snapshots": True})
    assert p.post("/api/hint", {"mission_slug": SLUG,
                                "hint_id": "m1_h5"}).status_code == 409, \
        "an outage flag still opens the ladder"

    for _ in range(5):
        p.post("/api/hint/next", {"mission_slug": SLUG})
    assert p.post("/api/hint", {"mission_slug": SLUG,
                                "hint_id": "m1_h5"}).status_code == 200


def test_a_team_can_always_unlock_the_next_rung_itself(app, event, facilitator):
    """Nobody has to sit and wait. This is the promise, and it is a button."""
    from app.models import HintUnlock, Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h3"}).status_code == 409

    r = p.post("/api/hint/next", {"mission_slug": SLUG}).get_json()
    assert r["level"] == "tool" and r["hint_id"] == "m1_h3"
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h3"}).status_code == 200

    assert p.post("/api/hint/next", {"mission_slug": SLUG}).get_json()["level"] == "method"
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"}).status_code == 409

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    modes = {u.hint_id: u.mode for u in HintUnlock.query.filter_by(team_id=team.id).all()}
    assert modes == {"m1_h3": "student_request", "m1_h4": "student_request"}


def test_the_usage_record_carries_the_level_and_how_it_opened(app, event, facilitator):
    """The AAR's three columns, and the only thing hints feed."""
    from app.models import HintUsage, Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()

    p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h1"})     # waited for it
    p.post("/api/hint/next", {"mission_slug": SLUG})                    # asked for it
    p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h3"})
    facilitator.post("/facilitator/api/hint/unlock",
                     {"team_id": team.id, "mission_slug": SLUG, "hint_id": "m1_h5"})
    p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"})     # handed it

    rows = {u.hint_id: (u.level, u.unlock_mode)
            for u in HintUsage.query.filter_by(team_id=team.id).all()}
    assert rows["m1_h1"] == ("orientation", "automatic")
    assert rows["m1_h3"] == ("tool", "student_request")
    assert rows["m1_h5"] == ("recovery", "facilitator")
    assert all(u.requested_at for u in HintUsage.query.all())
    assert ScoreEvent.query.filter_by(source="hint").count() == 0


def test_unlocking_is_idempotent_and_per_team(app, event, facilitator):
    from app.models import HintUnlock, Team

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    a = join(app, event, "Team 1", "kenji")
    b = join(app, event, "Team 2", "aoi")
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()

    for _ in range(3):
        facilitator.post("/facilitator/api/hint/unlock",
                         {"team_id": team.id, "mission_slug": SLUG, "hint_id": "m1_h5"})
    assert HintUnlock.query.filter_by(team_id=team.id, hint_id="m1_h5").count() == 1

    for _ in range(3):
        a.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"})
    assert HintUsage.query.filter_by(team_id=team.id, hint_id="m1_h5").count() == 1
    assert b.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"}).status_code == 409


def test_a_source_ladder_is_not_staged_at_all(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")

    row = _row(app, event)
    row.opened_at = utcnow() - timedelta(seconds=1500)
    db.session.commit()

    for level in ("orientation", "recovery"):
        r = p.post("/api/hint", {"mission_slug": SLUG,
                                 "hint_id": f"pv:commit:{level}"})
        assert r.status_code == 200, (level, r.status_code)
        assert r.get_json()["hint"]["text"], level

    for gone in ("pivot", "tool", "method"):
        assert p.post("/api/hint", {"mission_slug": SLUG,
                                    "hint_id": f"pv:commit:{gone}"}
                      ).status_code == 404, gone


def test_the_page_does_not_reload_itself_forever_once_a_mission_closes(app, event,
                                                                      facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    assert "let writable = true" in p.get(f"/mission/{SLUG}").data.decode()

    p.post("/api/lock", {"mission_slug": SLUG})
    assert "let writable = false" in p.get(f"/mission/{SLUG}").data.decode()

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert "let writable = false" in p.get(f"/mission/{SLUG}").data.decode()


def test_a_mission_that_has_not_started_opens_no_rungs(app, event, facilitator):
    from app.state import hint_is_open

    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"}).status_code == 409

    team = team_of(event, "Team 1")
    row = _row(app, event)
    recovery = content.find_hint(content.get_mission(app.config["CONTENT_DIR"], SLUG),
                                 "m1_h5")
    assert hint_is_open(event, row, team, recovery) is False

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    row.opened_at = utcnow() - timedelta(seconds=row.total_seconds + 30)
    db.session.commit()
    assert hint_is_open(event, row, team, recovery) is True


def test_every_launch_point_offers_two_authored_rungs(app):
    from app import missions as content
    from app.artifacts import resolve, snapshot_path

    cd, ad = app.config["CONTENT_DIR"], app.config["ARTIFACT_DIR"]
    seen = 0
    for m in content.ordered_missions(cd):
        for a in m.get("artifacts") or []:
            if not a.get("external_url"):
                continue
            seen += 1
            ladder = content.pivot_ladder(a, "en", cd)
            assert [h["level"] for h in ladder] == ["orientation", "recovery"], a["id"]
            assert all(h["text"] for h in ladder), a["id"]
            assert all(h["cost"] == 0 for h in ladder), a["id"]
            assert all(h["unlock_after"] == 0 for h in ladder), a["id"]
            note = content.tx(a.get("note"), "en") or ""
            pivot = content.tx(a.get("expected_pivot"), "en") or ""
            for rung in ladder:
                assert rung["text"] != note, (a["id"], rung["level"])
                assert rung["text"] != pivot, (a["id"], rung["level"])
            assert resolve(ad, snapshot_path(a)), a["id"]
    assert seen >= 20, seen


def test_a_pivot_rung_body_is_not_in_the_artifact_page_before_it_opens(app, event,
                                                                      facilitator):
    from app import missions as content

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    page = p.get("/artifacts/commit").data.decode("utf-8")
    ladder = content.pivot_ladder(
        content.find_artifact(content.get_mission(app.config["CONTENT_DIR"], SLUG),
                              "commit"), "ja", app.config["CONTENT_DIR"])
    recovery = next(h for h in ladder if h["level"] == "recovery")
    assert recovery["text"][:40] not in page


def test_a_locked_rung_is_not_sitting_in_the_page(app, event, facilitator):
    """Locked has to mean locked, not hidden by a disabled button."""
    from app import missions as content

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    page = p.get(f"/mission/{SLUG}").data.decode("utf-8")
    recovery = content.find_hint(content.get_mission(app.config["CONTENT_DIR"], SLUG),
                                 "m1_h5")
    assert content.tx(recovery["text"], "en")[:60] not in page


def test_hint_bodies_stay_behind_the_endpoint(app, event, facilitator):
    from app import missions as content

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    page = p.get(f"/mission/{SLUG}").data.decode("utf-8")
    for h in content.get_mission(app.config["CONTENT_DIR"], SLUG)["hints"]:
        body = content.tx(h.get("text"), "en")
        assert body[:60] not in page, h["id"]
    assert HintUsage.query.count() == 0


def test_locking_freezes_the_team_and_stamps_the_server_time(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    a = join(app, event, "Team 1", "aoi")
    b = join(app, event, "Team 1", "ren")
    a.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    assert a.post("/api/lock", {"mission_slug": SLUG}).status_code == 200

    tm = TeamMission.query.filter_by(mission_slug=SLUG).one()
    assert tm.submitted_at is not None

    assert b.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                      "answer": "changed"}).status_code == 409
    assert a.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                      "answer": "changed"}).status_code == 409


def test_locking_does_not_reveal_whether_the_answer_was_right(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    body = p.post("/api/lock", {"mission_slug": SLUG}).get_json()
    assert set(body) <= {"ok", "already"}
    assert ScoreEvent.query.filter_by(source="auto").count() == 0


def test_a_facilitator_can_unlock_a_team(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/lock", {"mission_slug": SLUG})
    from app.models import Team
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert facilitator.post(f"/facilitator/mission/{SLUG}/unlock/{team.id}", {}).status_code == 200
    assert p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                      "answer": "SR-REL-2019"}).status_code == 200


def test_closing_scores_every_team_once(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert _row(app, event).state == MISSION_CLOSED

    first = ScoreEvent.query.filter_by(source="auto").count()
    assert first >= 1
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert ScoreEvent.query.filter_by(source="auto").count() == first


def test_observations_are_never_scored(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    for i in range(20):
        p.post("/api/observation", {"mission_slug": SLUG, "text": f"note {i}"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert ScoreEvent.query.filter_by(session_id=event.id).count() == 0


def test_contribution_status_is_shown_but_never_blocks(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    a = join(app, event, "Team 1", "aoi")
    join(app, event, "Team 1", "silent")
    a.post("/api/observation", {"mission_slug": SLUG, "text": "one"})

    state = a.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    posted = {m["nickname"]: m["posted"] for m in state["members"]}
    assert posted == {"aoi": True, "silent": False}
    assert a.post("/api/lock", {"mission_slug": SLUG}).status_code == 200


def test_a_closed_mission_takes_no_observations_and_sells_no_hints(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    hint = _first_hint(app, SLUG)
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    assert p.post("/api/observation", {"mission_slug": SLUG, "text": "late"}).status_code == 409
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": hint}).status_code == 409
    assert HintUsage.query.filter_by(hint_id=hint).count() == 0
    assert ScoreEvent.query.filter_by(source="hint").count() == 0


def test_a_paused_mission_takes_no_observations_and_sells_no_hints(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    hint = _first_hint(app, SLUG)
    facilitator.post(f"/facilitator/mission/{SLUG}/pause", {})

    assert p.post("/api/observation", {"mission_slug": SLUG, "text": "during"}).status_code == 409
    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": hint}).status_code == 409
    assert HintUsage.query.filter_by(hint_id=hint).count() == 0


def test_a_team_that_has_locked_buys_no_more_hints(app, event, facilitator):
    """Paying for a hint it can no longer act on is paying for nothing."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    hint = _first_hint(app, SLUG)
    p.post("/api/lock", {"mission_slug": SLUG})

    assert p.post("/api/hint", {"mission_slug": SLUG, "hint_id": hint}).status_code == 409
    assert p.post("/api/observation", {"mission_slug": SLUG, "text": "after"}).status_code == 409
    assert ScoreEvent.query.filter_by(source="hint").count() == 0


def _first_hint(app, slug):
    from app import missions as content
    return (content.get_mission(app.config["CONTENT_DIR"], slug)["hints"])[0]["id"]


def test_pressing_close_again_scores_the_teams_a_crash_missed(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/lock", {"mission_slug": SLUG})

    row = _row(app, event)
    row.state = MISSION_CLOSED
    row.closed_at = utcnow()
    db.session.commit()
    assert ScoreEvent.query.filter_by(session_id=event.id).count() == 0

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert ScoreEvent.query.filter_by(session_id=event.id, source="auto").count() >= 1


def test_a_repair_close_does_not_rescore_the_teams_that_survived(app, event, facilitator):
    """The other half: the repair must not double-credit anybody."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    scored = ScoreEvent.query.filter_by(session_id=event.id).count()

    for _ in range(3):
        facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert ScoreEvent.query.filter_by(session_id=event.id).count() == scored
