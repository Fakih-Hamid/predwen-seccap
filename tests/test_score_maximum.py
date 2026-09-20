import json

import pytest

from app.scoring import COLLECTIVE_BONUS, score_maxima, team_score_parts

from .conftest import join

SLUG = "digital-footprint"
REQUIRED = ("hash", "domain", "process", "persistence", "file_path")


def maxima(app):
    return score_maxima(app.config["CONTENT_DIR"])


def publish(p, kind, value="x"):
    return p.post("/api/collective", {"type": kind, "value": value,
                                      "justification": "because"})


def validate_all(facilitator):
    board = facilitator.get("/facilitator/api/collective").get_json()
    out = []
    for row in board["entries"]:
        out.append(facilitator.post("/facilitator/api/collective/validate",
                                    {"ioc_id": row["id"], "validated": True}))
    return out


def test_the_three_numbers(app):
    m = maxima(app)
    assert m["base"] == 450.0
    assert m["collective_bonus"] == 15.0
    assert m["total"] == 465.0
    assert m["base"] + m["collective_bonus"] == m["total"]


def test_the_base_is_the_sum_of_the_authored_maxima(app):
    """Including the synthesis. Leaving it out gave a "maximum" of 300."""
    from app import missions as content
    cd = app.config["CONTENT_DIR"]
    got = sum(float(x.get("max_points", 0)) for x in content.ordered_missions(cd))
    final = content.final_definition(cd)
    got += float(final.get("max_points", 0)) if final else 0
    assert maxima(app)["base"] == got, "the base must be derived, not written down"
    assert got == 450.0


def test_the_bonus_is_the_one_the_scorer_awards(app):
    assert maxima(app)["collective_bonus"] == COLLECTIVE_BONUS


def test_with_no_bonus_a_team_is_all_base(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = next(t for t in event.teams if t.display_name == "Team 1")
    parts = team_score_parts(team)
    assert parts["collective"] == 0.0
    assert parts["base"] == parts["total"]
    assert parts["total"] > 0


def test_a_partly_covered_board_awards_nothing(app, event, facilitator):
    """Four of the five required categories is not four fifths of the bonus."""
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    people = {name: join(app, event, name, f"m{i}")
              for i, name in enumerate(["Team 1", "Team 2", "Team 3", "Team 4"], 1)}
    for name, kind in zip(sorted(people), REQUIRED[:4]):
        publish(people[name], kind)
    validate_all(facilitator)

    for name in people:
        team = next(t for t in event.teams if t.display_name == name)
        assert team_score_parts(team)["collective"] == 0.0


def test_the_full_bonus_reaches_every_team_once(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    names = ["Team 1", "Team 2", "Team 3", "Team 4", "Team 5"]
    people = {n: join(app, event, n, f"m{i}") for i, n in enumerate(names, 1)}
    for name, kind in zip(names, REQUIRED):
        publish(people[name], kind)
    validate_all(facilitator)

    for team in event.teams:
        parts = team_score_parts(team)
        assert parts["collective"] == COLLECTIVE_BONUS, team.display_name


def test_validating_again_does_not_pay_twice(app, event, facilitator):
    """The property that would silently inflate every total."""
    from app.models import ScoreEvent

    facilitator.post("/facilitator/session/state", {"collective_open": True})
    names = ["Team 1", "Team 2", "Team 3", "Team 4", "Team 5"]
    people = {n: join(app, event, n, f"m{i}") for i, n in enumerate(names, 1)}
    for name, kind in zip(names, REQUIRED):
        publish(people[name], kind)

    for _ in range(3):
        validate_all(facilitator)

    rows = ScoreEvent.query.filter_by(session_id=event.id,
                                      source="collective").all()
    assert len(rows) == len(list(event.teams)), rows
    for team in event.teams:
        assert team_score_parts(team)["collective"] == COLLECTIVE_BONUS


def test_a_team_can_never_exceed_the_applicable_maximum(app, event, facilitator):
    """base <= 450 and collective <= 15, so total <= 465."""
    m = maxima(app)
    facilitator.post("/facilitator/session/state", {"collective_open": True})
    names = ["Team 1", "Team 2", "Team 3", "Team 4", "Team 5"]
    people = {n: join(app, event, n, f"m{i}") for i, n in enumerate(names, 1)}
    for name, kind in zip(names, REQUIRED):
        publish(people[name], kind)
    validate_all(facilitator)

    for team in event.teams:
        parts = team_score_parts(team)
        assert parts["collective"] <= m["collective_bonus"]
        assert parts["total"] <= m["total"]


def test_the_scoreboard_api_reports_all_three(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/api/scoreboard").get_json()

    assert body["maxima"] == maxima(app)
    for row in body["rows"]:
        assert row["base"] + row["collective"] == pytest.approx(row["total"])


def readable(html):
    """`tojson` escapes non-ASCII, so Japanese reaches the page as \\uXXXX."""
    import re
    return re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), html)


@pytest.mark.parametrize("lang,fragment", [
    ("en", "class bonus"),
    ("ja", "クラス全体ボーナス"),
])
def test_the_board_carries_the_sentence(app, event, facilitator, lang, fragment):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    with p.client.session_transaction() as sess:
        sess["lang"] = lang
    html = readable(p.get("/scoreboard").get_data(as_text=True))

    assert fragment in html, fragment
    for placeholder in ("{base}", "{bonus}", "{total}"):
        assert placeholder in html, placeholder
    assert 'id="maxline"' in html
    assert "d.maxima.total" in html


def test_the_board_gets_real_numbers_to_put_in_it(app, event, facilitator):
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/api/scoreboard").get_json()
    assert body["maxima"]["total"] == 465.0


@pytest.mark.parametrize("lang,fragment", [("en", "465"), ("ja", "465")])
def test_the_console_says_it_too(facilitator, event, lang, fragment):
    with facilitator.client.session_transaction() as sess:
        sess["lang"] = lang
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    assert fragment in html


def test_the_results_export_carries_the_split(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    csv_text = facilitator.get("/facilitator/export/results.csv").get_data(as_text=True)
    assert "session,team,mission,source,category,points,question,actor,reason,at" in csv_text
    assert ("session,team,base_score,collective_bonus,total,"
            "max_base,max_collective_bonus,max_total") in csv_text
    assert "450.0,15.0,465.0" in csv_text
    assert csv_text.count("Team 1") >= 2


def test_the_session_export_carries_the_maxima(app, event, facilitator):
    doc = json.loads(facilitator.get("/facilitator/export/session.json")
                     .get_data(as_text=True))
    assert doc["session"]["maxima"] == maxima(app)


def test_the_mission_budgets_are_untouched(app):
    from app import missions as content
    cd = app.config["CONTENT_DIR"]
    for slug, expected in (("digital-footprint", 100), ("fake-infrastructure", 100),
                           ("threat-intelligence", 100)):
        assert content.get_mission(cd, slug)["max_points"] == expected
    assert content.final_definition(cd)["max_points"] == 150
