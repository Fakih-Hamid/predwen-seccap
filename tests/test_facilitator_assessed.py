import re

import pytest

from app import missions as content

from .conftest import join

SLUG = "threat-intelligence"
KEY = "m3_vt_interpretation"
RUBRIC = "m3_r_virustotal_limit"


def definition(app):
    return content.get_mission(app.config["CONTENT_DIR"], SLUG)


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def rubric_points(app):
    d = definition(app)
    item = next(r for r in d["rubric"] if r["key"] == RUBRIC)
    return float(item["points"])


def test_the_question_still_carries_no_automatic_points(app):
    q = content.find_question(definition(app), KEY)
    assert q["points"] == 0, "points must not be moved onto the question"


def test_the_rubric_item_still_carries_them(app):
    assert rubric_points(app) == 2


def test_the_rubric_item_still_claims_the_question(app):
    d = definition(app)
    item = next(r for r in d["rubric"] if r["key"] == RUBRIC)
    assert item["response_key"] == KEY


def test_the_budget_is_unchanged(app):
    d = definition(app)
    assert d["max_points"] == 100
    assert sum(d["budget"].values()) == 100


def test_the_displayed_value_comes_from_the_rubric(app):
    d = definition(app)
    payload = content.render_mission(d, "en")
    q = next(x for x in payload["questions"] if x["key"] == KEY)
    assert q["facilitator_points"] == rubric_points(app)

    bumped = {**d, "rubric": [dict(r, points=7) if r["key"] == RUBRIC else r
                              for r in d["rubric"]]}
    moved = content.render_mission(bumped, "en")
    moved_q = next(x for x in moved["questions"] if x["key"] == KEY)
    assert moved_q["facilitator_points"] == 7, (
        "the badge does not follow the rubric item")


def test_questions_with_their_own_points_are_unaffected(app):
    payload = content.render_mission(definition(app), "en")
    for q in payload["questions"]:
        if q["points"] or q["evidence_points"]:
            assert q["facilitator_points"] == 0, q["key"]


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_badge_shows_the_rubric_s_number(app, event, facilitator, lang):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    block = html.split(f'id="q-{KEY}"', 1)[1].split("</div>", 3)[0]
    assert "2" in block, block[:300]
    assert "facilitator assessed" not in block
    assert "ファシリテーター採点" not in block


def test_the_zero_is_gone_from_that_question(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    head = html.split(f'id="q-{KEY}"', 1)[1].split("</div>", 3)[0]
    assert not re.search(r">\s*0 pts\s*<", head), head


def test_every_other_question_still_shows_its_number(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    payload = content.render_mission(definition(app), "en")
    for q in payload["questions"]:
        if q["facilitator_points"]:
            continue
        total = int(round(q["points"] + q["evidence_points"]))
        block = html.split(f'id="q-{q["key"]}"', 1)[1].split("</div>", 3)[0]
        assert f"{total} pts" in block, (q["key"], total)


def test_no_question_explains_our_marking_process(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "facilitator assessed" not in html
    assert "ファシリテーター採点" not in html
    assert 'class="qp graded"' not in html
