import datetime
import re

import pytest
import yaml
from werkzeug.security import generate_password_hash

from app import create_app
from app.auth import SESSION_ASSISTANT, SESSION_FACILITATOR, SESSION_MEMBER
from app.models import Mission, Team, aware, db
from app.state import create_session

from .conftest import FACILITATOR_PASSWORD, Participant, join, open_all_sources

ASSISTANT_PASSWORD = "ta-rehearsal-only"
SLUG = "digital-footprint"


@pytest.fixture()
def ta_app():
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "ASSISTANT_PASSWORD_HASH": generate_password_hash(ASSISTANT_PASSWORD),
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
    })
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()


@pytest.fixture()
def ta_event(ta_app):
    ev = create_session(ta_app.config, title="TA test", code="TAAAAA")
    db.session.commit()
    return ev


def _client_with_csrf(app):
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    return Participant(client, token, None, "ta")


def facilitator_on(app):
    p = _client_with_csrf(app)
    res = p.client.post("/facilitator/login",
                        data={"csrf_token": p.csrf, "password": FACILITATOR_PASSWORD})
    assert res.status_code in (302, 303)
    return p


def assistant_on(app, team_name=None):
    p = _client_with_csrf(app)
    res = p.client.post("/assistant/login",
                        data={"csrf_token": p.csrf, "password": ASSISTANT_PASSWORD})
    assert res.status_code in (302, 303), res.data[:200]
    if team_name is not None:
        team = Team.query.filter_by(display_name=team_name).one()
        res = p.client.post("/assistant/team",
                            data={"csrf_token": p.csrf, "team_id": team.id})
        assert res.status_code in (302, 303)
    return p


def wind(ev, slug, seconds):
    row = Mission.query.filter_by(session_id=ev.id, slug=slug).one()
    row.opened_at = aware(row.opened_at) - datetime.timedelta(seconds=seconds)
    db.session.commit()


def test_the_assistant_password_is_its_own_secret(ta_app, ta_event):
    p = _client_with_csrf(ta_app)
    res = p.client.post("/assistant/login",
                        data={"csrf_token": p.csrf, "password": FACILITATOR_PASSWORD})
    assert res.status_code == 401
    res = p.client.post("/facilitator/login",
                        data={"csrf_token": p.csrf, "password": ASSISTANT_PASSWORD})
    assert res.status_code == 401


def test_signing_in_sets_the_assistant_flag_and_nothing_else(ta_app, ta_event):
    p = assistant_on(ta_app)
    with p.client.session_transaction() as sess:
        assert sess.get(SESSION_ASSISTANT) is True
        assert not sess.get(SESSION_FACILITATOR)
        assert not sess.get(SESSION_MEMBER)


def test_signing_in_as_a_ta_drops_a_participant_seat(ta_app, ta_event):
    """A TA who joined a team during the morning must not keep writing to it."""
    p = join(ta_app, ta_event, "Team 1", "ta-who-joined")
    with p.client.session_transaction() as sess:
        assert sess.get(SESSION_MEMBER)
    res = p.client.post("/assistant/login",
                        data={"csrf_token": p.csrf, "password": ASSISTANT_PASSWORD})
    assert res.status_code in (302, 303)
    with p.client.session_transaction() as sess:
        assert not sess.get(SESSION_MEMBER)
    assert p.get("/api/mission-state?mission_slug=" + SLUG).status_code == 401


def test_an_empty_hash_disables_the_role_rather_than_opening_it():
    application = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test", "ASSISTANT_PASSWORD_HASH": "",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "DEFAULT_LANG": "en", "ADMIN_BYPASS": False,
    })
    with application.app_context():
        db.create_all()
        p = _client_with_csrf(application)
        body = p.client.get("/assistant/login").get_data(as_text=True)
        assert "TA access is disabled" in body
        for password in ("", ASSISTANT_PASSWORD, "anything"):
            res = p.client.post("/assistant/login",
                                data={"csrf_token": p.csrf, "password": password})
            assert res.status_code == 401
        db.session.remove()


def test_the_bypass_never_previews_for_an_assistant(ta_event):
    application = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test", "ADMIN_BYPASS": True,
        "ASSISTANT_PASSWORD_HASH": generate_password_hash(ASSISTANT_PASSWORD),
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "DEFAULT_LANG": "en",
    })
    with application.app_context():
        db.create_all()
        create_session(application.config, title="Bypass", code="TABYPA")
        db.session.commit()
        p = assistant_on(application)
        res = p.client.get("/mission/" + SLUG)
        assert res.status_code in (302, 303)
        assert "/join" in res.headers["Location"]
        db.session.remove()


FACILITATOR_READS = ("/facilitator/", "/facilitator/api/progress",
                     "/facilitator/export/session.json")
FACILITATOR_WRITES = (
    ("/facilitator/mission/%s/open" % SLUG, {}),
    ("/facilitator/mission/%s/close" % SLUG, {}),
    ("/facilitator/mission/%s/pause" % SLUG, {}),
    ("/facilitator/mission/%s/extend" % SLUG, {"seconds": 300}),
    ("/facilitator/session/state", {"scoreboard_visible": True}),
    ("/facilitator/session/reset", {}),
    ("/facilitator/api/hint/unlock", {"team_id": 1, "mission_slug": SLUG,
                                      "hint_id": "m1_h5"}),
)


def test_an_assistant_reads_no_facilitator_page(ta_app, ta_event):
    p = assistant_on(ta_app, "Team 1")
    for path in FACILITATOR_READS:
        res = p.client.get(path)
        assert res.status_code in (302, 303), path
        assert "/facilitator/login" in res.headers["Location"], path


def test_an_assistant_writes_nothing_the_facilitator_can(ta_app, ta_event):
    p = assistant_on(ta_app, "Team 1")
    for path, body in FACILITATOR_WRITES:
        res = p.post(path, body)
        assert res.status_code == 403, (path, res.status_code)
    row = Mission.query.filter_by(session_id=ta_event.id, slug=SLUG).one()
    assert row.state == "locked"
    assert ta_event.scoreboard_visible is False


def test_grading_and_the_score_lock_are_out_of_reach(ta_app, ta_event):
    p = assistant_on(ta_app, "Team 1")
    team = Team.query.filter_by(session_id=ta_event.id, display_name="Team 1").one()
    res = p.post("/facilitator/api/grade",
                 {"team_id": team.id, "mission_slug": SLUG,
                  "rubric_key": "m1_r_separation", "points": 12, "reason": "x"})
    assert res.status_code == 403
    assert p.client.get("/facilitator/grade/%d" % team.id).status_code in (302, 303)


def opened(ta_app, ta_event, slug=SLUG):
    fac = facilitator_on(ta_app)
    assert fac.post("/facilitator/mission/%s/open" % slug, {}).status_code == 200
    return fac


def test_a_ta_picks_a_team_and_sees_only_that_team(ta_app, ta_event):
    opened(ta_app, ta_event)
    join(ta_app, ta_event, "Team 1", "aoi").post(
        "/api/observation", {"mission_slug": SLUG, "artifact_id": "commit",
                             "text": "team one saw two files change"})
    join(ta_app, ta_event, "Team 2", "ren").post(
        "/api/observation", {"mission_slug": SLUG, "artifact_id": "commit",
                             "text": "team two wrote something else"})

    p = assistant_on(ta_app, "Team 1")
    d = p.get("/assistant/api/board").get_json()
    assert d["team"]["name"] == "Team 1"
    texts = [o["text"] for o in d["current"]["observations"]]
    assert "team one saw two files change" in texts
    assert "team two wrote something else" not in texts
    assert [m["nickname"] for m in d["team"]["members"]] == ["aoi"]


def test_the_board_shows_what_the_team_wrote_and_whether_evidence_is_attached(
        ta_app, ta_event):
    opened(ta_app, ta_event)
    kenji = join(ta_app, ta_event, "Team 1", "kenji")
    kenji.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_flag",
                                   "answer": "ALLOW_UNSIGNED_RECOVERY"})
    kenji.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_test",
                                   "answer": "test_added", "reasoning": "we think"})
    kenji.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_flag",
                                 "artifact_id": "commit",
                                 "source_url": "https://github.com/x",
                                 "excerpt": "ALLOW_UNSIGNED_RECOVERY = True"})

    p = assistant_on(ta_app, "Team 1")
    d = p.get("/assistant/api/board").get_json()
    by_key = {a["key"]: a for a in d["current"]["answers"]}

    assert by_key["m1_flag"]["answer"] == "ALLOW_UNSIGNED_RECOVERY"
    assert by_key["m1_flag"]["evidence"][0]["excerpt"] == "ALLOW_UNSIGNED_RECOVERY = True"
    assert by_key["m1_flag"]["evidence"][0]["url"] == "https://github.com/x"
    assert by_key["m1_test"]["answer"] == "A new test was added"
    assert by_key["m1_test"]["reasoning"] == "we think"
    assert by_key["m1_test"]["evidence"] == []
    assert by_key["m1_key_id"]["answer"] is None


def test_the_board_carries_no_answer_key(ta_app, ta_event):
    opened(ta_app, ta_event)
    join(ta_app, ta_event, "Team 1", "kenji")
    p = assistant_on(ta_app, "Team 1")

    tree = yaml.safe_load(
        (__import__("pathlib").Path(__file__).resolve().parents[1]
         / "content" / "missions" / "m1-digital-footprint.yaml")
        .read_text(encoding="utf-8"))
    secrets = set()
    for q in tree["questions"]:
        for value in (q.get("validator") or {}).get("accept") or []:
            secrets.add(str(value))
    for r in tree["rubric"]:
        secrets.add(r["criteria"]["en"])
        secrets.add(r["criteria"]["ja"])

    for body in (p.get("/assistant/api/board").get_data(as_text=True),
                 p.client.get("/assistant/board").get_data(as_text=True)):
        for secret in secrets:
            assert secret not in body, secret[:40]
        assert "\"correct\"" not in body
        assert "validator" not in body
        assert "criteria" not in body


def test_the_board_shows_which_sources_have_arrived(ta_app, ta_event):
    opened(ta_app, ta_event)
    join(ta_app, ta_event, "Team 1", "kenji")
    p = assistant_on(ta_app, "Team 1")

    d = p.get("/assistant/api/board").get_json()
    by_id = {s["id"]: s for s in d["current"]["sources"]}
    assert by_id["commit"]["arrived"] is True
    assert by_id["username-collision"]["arrived"] is False
    assert "arrives_in" not in by_id["username-collision"]

    open_all_sources(ta_app, ta_event, SLUG)
    d = p.get("/assistant/api/board").get_json()
    assert all(s["arrived"] for s in d["current"]["sources"])


def test_the_board_takes_no_action_the_unlock_endpoint_is_gone(ta_app, ta_event):
    opened(ta_app, ta_event)
    join(ta_app, ta_event, "Team 1", "kenji")
    p = assistant_on(ta_app, "Team 1")

    assert p.post("/assistant/api/hint/unlock", {}).status_code == 404
    d = p.get("/assistant/api/board").get_json()
    assert "next_locked" not in d["current"]


LEVELS = ("orientation", "pivot", "tool", "method", "recovery")


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_no_internal_level_id_reaches_a_ta_or_a_participant_as_text(
        ta_app, ta_event, lang):
    opened(ta_app, ta_event)
    kenji = join(ta_app, ta_event, "Team 1", "kenji")
    p = assistant_on(ta_app, "Team 1")
    for client in (p.client, kenji.client):
        with client.session_transaction() as sess:
            sess["lang"] = lang

    board = p.client.get("/assistant/board").get_data(as_text=True)
    wording = " ".join(re.findall(r'data-t-[a-z-]+="([^"]*)"', board))
    for level in LEVELS:
        assert not re.search(rf"\b{level}\b", wording), (lang, level)

    if lang == "ja":
        mission = kenji.get("/mission/" + SLUG).get_data(as_text=True)
        visible = re.sub(r"<script.*?</script>", "", mission, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible)
        for level in LEVELS:
            assert not re.search(rf"\b{level}\b", visible), level


def test_a_team_of_another_session_cannot_be_chosen(ta_app, ta_event):
    other = create_session(ta_app.config, title="Other", code="OTHERA")
    db.session.commit()
    stranger = Team.query.filter_by(session_id=other.id).first()
    p = assistant_on(ta_app)
    mine = Team.query.filter_by(session_id=ta_event.id).first()
    res = p.client.post("/assistant/team",
                        data={"csrf_token": p.csrf, "team_id": mine.id})
    assert res.status_code in (302, 303)
    assert "/assistant/pick" in res.headers["Location"]
    assert p.get("/assistant/api/board").status_code == 404
    res = p.client.post("/assistant/team",
                        data={"csrf_token": p.csrf, "team_id": stranger.id})
    assert "/assistant/board" in res.headers["Location"]


def test_the_facilitator_reaches_every_board(ta_app, ta_event):
    fac = opened(ta_app, ta_event)
    team = Team.query.filter_by(session_id=ta_event.id, display_name="Team 3").one()
    res = fac.client.post("/assistant/team",
                          data={"csrf_token": fac.csrf, "team_id": team.id})
    assert res.status_code in (302, 303)
    assert fac.get("/assistant/api/board").get_json()["team"]["name"] == "Team 3"


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Observations"),
    ("ja", "観察メモ"),
])
def test_the_board_renders_in_both_languages(ta_app, ta_event, lang, fragment):
    opened(ta_app, ta_event)
    p = assistant_on(ta_app, "Team 1")
    with p.client.session_transaction() as sess:
        sess["lang"] = lang
    body = p.client.get("/assistant/board").get_data(as_text=True)
    assert fragment in body
    assert "/facilitator/" not in body


def test_an_anonymous_browser_is_sent_to_the_ta_sign_in(ta_app, ta_event):
    p = _client_with_csrf(ta_app)
    res = p.client.get("/assistant/board")
    assert res.status_code in (302, 303)
    assert "/assistant/login" in res.headers["Location"]
    assert p.post("/assistant/api/hint/unlock", {}).status_code == 404
