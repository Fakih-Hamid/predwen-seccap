import json

import pytest

from .conftest import join, open_all_sources, wind_forward

SIZES = (4, 4, 4, 5, 5, 5)
CLASS_SIZE = sum(SIZES)
MISSIONS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")

ANSWERS = {
    "digital-footprint": {
        "m1_key_id": "SR-REL-2019",
        "m1_flag": "ALLOW_UNSIGNED_RECOVERY",
        "m1_test": "assertions_removed",
        "m1_collision": "unrelated",
    },
    "fake-infrastructure": {
        "m2_campaign": "KITSUNE-42",
        "m2_signature": "absent_expected_key",
        "m2_failover_host": "archive.sakura-vpn-update.com",
    },
    "threat-intelligence": {
        "m3_execution_time": "00:19:16",
        "m3_exfil": "archive.sakura-vpn-update.com",
        "m3_scope": ["srdev077"],
    },
}
FIRST_ARTIFACT = {"digital-footprint": "commit",
                  "fake-infrastructure": "update-service",
                  "threat-intelligence": "evidence-pack"}


@pytest.fixture
def room(app, event, facilitator):
    """Twenty-seven students in six uneven teams, joined through the form."""
    from app.models import Team

    with app.app_context():
        names = [t.display_name for t in
                 Team.query.filter_by(session_id=event.id).order_by(Team.id)]
    seats = {}
    for name, size in zip(names, SIZES):
        seats[name] = [join(app, event, name, f"{name.replace(' ', '')}-{i}")
                       for i in range(size)]
    return names, seats


def test_the_room_is_the_size_it_should_be(room, app, event):
    from app.models import Member

    names, seats = room
    assert sum(len(v) for v in seats.values()) == CLASS_SIZE == 27
    with app.app_context():
        assert Member.query.count() == CLASS_SIZE
    assert sorted(len(v) for v in seats.values()) == [4, 4, 4, 5, 5, 5]


def test_the_whole_afternoon(room, app, event, facilitator):
    """Three missions, every team, ending with a ranked board and an export."""
    from app.models import ScoreEvent, Team

    names, seats = room

    for slug in MISSIONS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)

        for name in names:
            members = seats[name]
            for i, p in enumerate(members):
                assert p.get(f"/mission/{slug}").status_code == 200
                r = p.post("/api/observation",
                           {"mission_slug": slug, "text": f"{name} note {i}"})
                assert r.get_json()["ok"]

            for i, (key, value) in enumerate(ANSWERS[slug].items()):
                writer = members[i % len(members)]
                assert writer.post("/api/submission",
                                   {"mission_slug": slug, "question_key": key,
                                    "answer": value}).get_json()["ok"]
                assert writer.post("/api/evidence",
                                   {"mission_slug": slug, "question_key": key,
                                    "artifact_id": FIRST_ARTIFACT[slug],
                                    "excerpt": "read here"}).get_json()["ok"]

            assert members[-1].post("/api/hint/next",
                                    {"mission_slug": slug}).get_json()["ok"]
            assert members[0].post("/api/lock",
                                   {"mission_slug": slug}).get_json()["ok"]

        facilitator.post(f"/facilitator/mission/{slug}/close", {})

    facilitator.post("/facilitator/session/state", {"collective_open": True})
    for i, name in enumerate(names):
        r = seats[name][0].post("/api/collective",
                                {"type": "domain",
                                 "value": f"host-{i}.sakura-vpn-update.com",
                                 "justification": "seen in the proxy log"})
        assert r.get_json()["ok"], r.get_json()

    facilitator.post("/facilitator/session/state", {"final_open": True})
    for name in names:
        p = seats[name][0]
        assert p.get("/final").status_code == 200
        assert p.post("/api/final", {"verdict": f"{name} verdict",
                                     "confidence": "medium"}
                      ).get_json()["ok"]

    with app.app_context():
        for name in names:
            team = Team.query.filter_by(session_id=event.id,
                                        display_name=name).one()
            assert ScoreEvent.query.filter_by(team_id=team.id).count() > 0, name

    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    board = seats[names[0]][0].get("/api/scoreboard").get_json()
    assert board["visible"] is True
    assert len(board["rows"]) == len(names) == 6

    for kind in ("results.csv", "session.json"):
        r = facilitator.get(f"/facilitator/export/{kind}")
        assert r.status_code == 200, kind
        assert r.data, kind


def test_a_four_person_team_finishes_the_same_as_a_five(room, app, event,
                                                       facilitator):
    names, seats = room
    fives = [n for n, s in zip(names, SIZES) if s == 5]
    fours = [n for n, s in zip(names, SIZES) if s == 4]
    assert fives and fours

    for slug in MISSIONS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)
        for name in fives[:1] + fours[:1]:
            p = seats[name][0]
            for key, value in ANSWERS[slug].items():
                p.post("/api/submission", {"mission_slug": slug,
                                           "question_key": key, "answer": value})
                p.post("/api/evidence", {"mission_slug": slug,
                                         "question_key": key,
                                         "artifact_id": FIRST_ARTIFACT[slug],
                                         "excerpt": "read here"})
            p.post("/api/lock", {"mission_slug": slug})
        facilitator.post(f"/facilitator/mission/{slug}/close", {})

    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    five = seats[fives[0]][0].get("/api/my-score").get_json()["breakdown"]
    four = seats[fours[0]][0].get("/api/my-score").get_json()["breakdown"]
    assert five == four, (five, four)


def test_six_tas_watch_their_tables_without_taking_a_seat(room, app, event,
                                                          facilitator):
    from app.models import Member, Team
    from werkzeug.security import generate_password_hash

    names, _ = room
    app.config["ASSISTANT_PASSWORD_HASH"] = generate_password_hash("ta-pw")

    import re
    tas = []
    for name in names:
        client = app.test_client()
        html = client.get("/assistant/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        assert client.post("/assistant/login",
                           data={"password": "ta-pw",
                                 "csrf_token": token}).status_code == 302
        with app.app_context():
            team_id = Team.query.filter_by(session_id=event.id,
                                           display_name=name).one().id
        assert client.post("/assistant/team",
                           data={"team_id": team_id,
                                 "csrf_token": token}).status_code == 302
        tas.append((name, client))

    with app.app_context():
        assert Member.query.count() == CLASS_SIZE, "a TA took a seat"

    for name, client in tas:
        board = client.get("/assistant/api/board").get_json()
        assert board["team"]["name"] == name
        assert len(board["team"]["members"]) == SIZES[names.index(name)]
        with client.session_transaction() as sess:
            assert "member_token" not in sess
            assert not sess.get("is_facilitator")
        assert client.get("/facilitator/api/progress").status_code in (302, 303)


def test_a_ta_cannot_answer_or_score_for_a_team(app, event, facilitator):
    """The console changes the shape of the day; it does not play the game."""
    slug = MISSIONS[0]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)

    for path, body in (("/api/submission", {"mission_slug": slug,
                                            "question_key": "m1_key_id",
                                            "answer": "SR-REL-2019"}),
                       ("/api/observation", {"mission_slug": slug, "text": "x"}),
                       ("/api/lock", {"mission_slug": slug})):
        r = facilitator.post(path, body)
        assert r.status_code == 401, (path, r.status_code)


def test_everything_written_survives_a_restart(room, app, event, facilitator):
    from app.models import Observation, Submission, Team, db

    names, seats = room
    slug = MISSIONS[0]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    p = seats[names[0]][0]
    p.post("/api/submission", {"mission_slug": slug,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/observation", {"mission_slug": slug, "text": "before restart"})
    p.post("/api/lock", {"mission_slug": slug})

    with app.app_context():
        db.session.expire_all()
        db.session.remove()
        team = Team.query.filter_by(session_id=event.id,
                                    display_name=names[0]).one()
        assert Submission.query.filter_by(team_id=team.id).count() >= 1
        assert Observation.query.filter_by(team_id=team.id).count() >= 1

    state = p.get(f"/api/mission-state?mission_slug={slug}").get_json()
    assert state["answers"]["m1_key_id"]["answer"] == "SR-REL-2019"
    assert state["progress"]["answered"] >= 1


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_every_screen_renders_in_both_languages_at_size(room, app, event,
                                                        facilitator, lang):
    names, seats = room
    for slug in MISSIONS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)
    facilitator.post("/facilitator/session/state",
                     {"collective_open": True, "final_open": True,
                      "scoreboard_visible": True})

    p = seats[names[0]][0]
    with p.client.session_transaction() as sess:
        sess["lang"] = lang

    for path in ("/team", "/briefing", "/evidence",
                 "/collective-intel", "/final", "/scoreboard", "/presentation",
                 f"/mission/{MISSIONS[0]}", "/artifacts/commit",
                 "/artifacts/token-audit"):
        r = p.get(path)
        assert r.status_code == 200, (path, lang, r.status_code)
        html = r.get_data(as_text=True)
        assert f'<html lang="{lang}"' in html, (path, lang)
        assert "⟦" not in html, (path, lang,
                                 [s for s in html.split("⟦")[1:2]])


def test_the_room_is_online_and_there_is_no_way_to_make_it_otherwise(
        room, app, event, facilitator):
    names, seats = room
    slug = MISSIONS[0]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    p = seats[names[0]][0]

    console = facilitator.get("/facilitator/").get_data(as_text=True)
    assert 'data-flag="fallback_snapshots"' not in console
    assert "offline_mode" not in facilitator.get(
        "/facilitator/api/progress").get_json()["status"]

    facilitator.post("/facilitator/session/state", {"fallback_snapshots": True})
    live = p.get("/artifacts/commit").get_data(as_text=True)
    assert "Open externally" in live or "外部サイトを開く" in live
    assert "Saved copy" not in live and "保存済みコピー" not in live

    internal = p.get("/artifacts/token-audit").get_data(as_text=True)
    assert "Saved copy" not in internal and "保存済みコピー" not in internal
    assert "Open externally" in internal or "外部サイトを開く" in internal

    p.post("/api/submission", {"mission_slug": slug,
                               "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{slug}/close", {})
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    breakdown = p.get("/api/my-score").get_json()["breakdown"]
    assert breakdown["by_category"].get("correctness", 0) > 0, breakdown
