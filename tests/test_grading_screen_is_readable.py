import pytest

from .conftest import join

SLUG = "digital-footprint"


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def team_id(event, display_name):
    from app.models import Team
    return Team.query.filter_by(session_id=event.id,
                                display_name=display_name).one().id


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_every_authored_question_has_an_expected_answer(app, lang):
    from app import missions as content
    from app.facilitator.routes import expected_answer

    with app.app_context():
        cd = app.config["CONTENT_DIR"]
        for m in content.ordered_missions(cd) + [content.final_definition(cd)]:
            for q in m.get("questions") or []:
                got = expected_answer(q, lang, m)
                assert got, "%s/%s has nothing to mark against" % (m["slug"],
                                                                   q["key"])


def test_the_free_text_question_shows_the_criteria_it_is_marked_on(app):
    from app import missions as content
    from app.facilitator.routes import expected_answer

    with app.app_context():
        cd = app.config["CONTENT_DIR"]
        m = content.get_mission(cd, "threat-intelligence")
        q = content.find_question(m, "m3_vt_interpretation")
        expected = expected_answer(q, "en", m)

    assert "safe or malicious from the VirusTotal result alone" in expected
    assert "SBX-K42" in expected


def test_the_rules_are_shown_where_an_ordering_has_no_single_key(app):
    from app import missions as content
    from app.facilitator.routes import expected_answer

    with app.app_context():
        cd = app.config["CONTENT_DIR"]
        final = content.final_definition(cd)
        q = content.find_question(final, "final_response_now")
        expected = expected_answer(q, "en", final)

    assert "exactly 5" in expected
    assert "first: Isolate SR-DEV-077" in expected
    assert "never: Wipe the host before preserving it" in expected
    assert "a_isolate" not in expected


def test_a_choice_answer_is_read_back_in_words_not_option_ids(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")

    from app import missions as content
    with app.app_context():
        m = content.get_mission(app.config["CONTENT_DIR"], SLUG)
        q = content.find_question(m, "m1_test")
        chosen = next(o for o in q["options"] if o.get("correct"))

    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_test",
                               "answer": chosen["id"]})

    with app.app_context():
        tid = team_id(event, "Team 1")
    html = facilitator.get(f"/facilitator/grade/{tid}").get_data(as_text=True)

    assert chosen["label"]["en"] in html
    assert ">" + chosen["id"] + "<" not in html


def test_a_multi_choice_answer_is_read_back_as_a_list_of_labels(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")

    from app import missions as content
    with app.app_context():
        m = content.get_mission(app.config["CONTENT_DIR"], SLUG)
        q = content.find_question(m, "m1_exif")
        picked = [o for o in q["options"] if o.get("correct")]

    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_exif",
                               "answer": [o["id"] for o in picked]})

    with app.app_context():
        tid = team_id(event, "Team 1")
    html = facilitator.get(f"/facilitator/grade/{tid}").get_data(as_text=True)

    for option in picked:
        assert option["label"]["en"] in html, option["id"]
    assert "[&#39;" not in html


def test_one_team_locking_does_not_lock_another(app, event, facilitator):
    open_mission(facilitator)
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aiko")

    assert one.post("/api/lock", {"mission_slug": SLUG}).status_code == 200

    first = one.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert first["team_locked"] is True
    assert first["mission"]["writable"] is False
    refused = one.post("/api/submission", {"mission_slug": SLUG,
                                           "question_key": "m1_flag",
                                           "answer": "ALLOW_UNSIGNED_RECOVERY"})
    assert refused.status_code == 409

    second = two.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert second["team_locked"] is False
    assert second["mission"]["writable"] is True
    accepted = two.post("/api/submission", {"mission_slug": SLUG,
                                            "question_key": "m1_flag",
                                            "answer": "ALLOW_UNSIGNED_RECOVERY"})
    assert accepted.status_code == 200

    from app.state import mission_row
    with app.app_context():
        assert mission_row(event, SLUG).state == "open"


def test_the_lock_banner_is_only_on_the_team_that_locked(app, event,
                                                         facilitator):
    open_mission(facilitator)
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aiko")
    one.post("/api/lock", {"mission_slug": SLUG})

    locked = one.get(f"/mission/{SLUG}").get_data(as_text=True)
    still_open = two.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "submission is locked." in locked
    assert 'id="lock"' not in locked, "the button goes once there is nothing to lock"
    assert "submission is locked." not in still_open
    assert 'id="lock"' in still_open


def test_the_final_lock_is_per_team_too(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aiko")

    one.post("/api/final", {"verdict": "ours"})
    assert one.post("/api/final/lock", {}).status_code == 200

    assert one.post("/api/final", {"verdict": "changed"}).status_code == 409
    assert two.post("/api/final", {"verdict": "still writing"}).status_code == 200
