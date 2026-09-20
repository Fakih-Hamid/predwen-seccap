import pytest

from app import missions as content
from app.scoring import check_answer

from .conftest import join

QUESTIONS = {
    "m1_exif": ("digital-footprint",
                ["artist", "datetime", "model"], ["gps", "serial"]),
    "m2_strongest_link": ("fake-infrastructure",
                          ["headers", "hash", "campaign"],
                          ["shared_ips", "same_ns", "recent_reg"]),
    "m3_persistence": ("threat-intelligence",
                       ["run_key", "sched_task"], ["service"]),
    "m3_attack": ("threat-intelligence",
                  ["t1059_001", "t1547_001", "t1053_005", "t1071_001",
                   "t1560_001", "t1041"],
                  ["t1195_002", "t1486", "t1110_003"]),
}


def question(app, key):
    slug = QUESTIONS[key][0]
    definition = content.get_mission(app.config["CONTENT_DIR"], slug)
    q = content.find_question(definition, key)
    assert q is not None, key
    return q


def fraction(app, key, answer):
    return check_answer(question(app, key), answer)[1]


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_canonical_answer_is_unchanged(app, key):
    _, right, wrong = QUESTIONS[key]
    q = question(app, key)
    correct = [o["id"] for o in q["options"] if o.get("correct")]
    others = [o["id"] for o in q["options"] if not o.get("correct")]
    assert sorted(correct) == sorted(right)
    assert sorted(others) == sorted(wrong)


@pytest.mark.parametrize("key,points", [("m1_exif", 4), ("m2_strongest_link", 4),
                                        ("m3_persistence", 5), ("m3_attack", 6)])
def test_the_maximum_is_unchanged(app, key, points):
    assert question(app, key)["points"] == points


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_exact_answer_is_full_marks(app, key):
    _, right, _ = QUESTIONS[key]
    correct, frac = check_answer(question(app, key), list(right))
    assert correct is True
    assert frac == 1.0


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_empty_answer_is_zero(app, key):
    assert fraction(app, key, []) == 0.0


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_one_right_option_only(app, key):
    _, right, wrong = QUESTIONS[key]
    expected = round(1 / len(right), 4)
    assert fraction(app, key, [right[0]]) == expected
    assert 0 < expected < 1


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_incomplete_but_all_right(app, key):
    _, right, _ = QUESTIONS[key]
    picked = right[:-1]
    assert fraction(app, key, picked) == round(len(picked) / len(right), 4)


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_all_right_plus_one_wrong(app, key):
    _, right, wrong = QUESTIONS[key]
    expected = round(max(0.0, 1.0 - 1 / len(wrong)), 4)
    assert fraction(app, key, list(right) + [wrong[0]]) == expected


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_only_wrong_options_is_zero(app, key):
    _, _, wrong = QUESTIONS[key]
    assert fraction(app, key, list(wrong)) == 0.0


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_selecting_everything_is_zero(app, key):
    """The case the old denominator got wrong. `m3_attack` paid 0.5 for it."""
    _, right, wrong = QUESTIONS[key]
    assert fraction(app, key, list(right) + list(wrong)) == 0.0


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_the_fraction_never_leaves_the_unit_interval(app, key):
    import itertools
    _, right, wrong = QUESTIONS[key]
    every = list(right) + list(wrong)
    for size in range(len(every) + 1):
        for picked in itertools.combinations(every, size):
            f = fraction(app, key, list(picked))
            assert 0.0 <= f <= 1.0, (picked, f)


@pytest.mark.parametrize("key", sorted(QUESTIONS))
def test_adding_a_wrong_option_never_helps(app, key):
    _, right, wrong = QUESTIONS[key]
    for size in range(len(right) + 1):
        base = right[:size]
        assert fraction(app, key, base + [wrong[0]]) <= fraction(app, key, base)


def test_m3_attack_is_the_case_that_changed(app):
    """Six right, three wrong. Selecting all nine used to be worth half."""
    _, right, wrong = QUESTIONS["m3_attack"]
    assert (len(right), len(wrong)) == (6, 3)
    assert fraction(app, "m3_attack", right + wrong) == 0.0
    assert fraction(app, "m3_attack", right[:5]) == round(5 / 6, 4)
    assert fraction(app, "m3_attack", right + wrong[:1]) == round(2 / 3, 4)


def test_partial_credit_reaches_the_real_score(app, event, facilitator):
    from app.models import ScoreEvent

    slug = "threat-intelligence"
    _, right, wrong = QUESTIONS["m3_attack"]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})

    good = join(app, event, "Team 1", "kenji")
    half = join(app, event, "Team 2", "aoi")
    greedy = join(app, event, "Team 3", "rin")

    good.post("/api/submission", {"mission_slug": slug,
                                  "question_key": "m3_attack",
                                  "answer": right})
    half.post("/api/submission", {"mission_slug": slug,
                                  "question_key": "m3_attack",
                                  "answer": right[:3]})
    greedy.post("/api/submission", {"mission_slug": slug,
                                    "question_key": "m3_attack",
                                    "answer": right + wrong})

    facilitator.post(f"/facilitator/mission/{slug}/close", {})

    def earned(team_name):
        team = next(t for t in event.teams if t.display_name == team_name)
        rows = ScoreEvent.query.filter_by(team_id=team.id,
                                          question_key="m3_attack").all()
        return round(sum(r.points for r in rows), 2)

    assert earned("Team 1") == 6.0                     # 6 pts, all right
    assert earned("Team 2") == 3.0                     # 3/6 of 6
    assert earned("Team 3") == 0.0                     # everything selected


def test_a_greedy_team_does_not_outscore_a_careful_one(app, event, facilitator):
    """The property that matters pedagogically."""
    from app.models import ScoreEvent

    slug = "threat-intelligence"
    _, right, wrong = QUESTIONS["m3_persistence"]
    facilitator.post(f"/facilitator/mission/{slug}/open", {})

    careful = join(app, event, "Team 1", "kenji")
    greedy = join(app, event, "Team 2", "aoi")
    careful.post("/api/submission", {"mission_slug": slug,
                                     "question_key": "m3_persistence",
                                     "answer": [right[0]]})
    greedy.post("/api/submission", {"mission_slug": slug,
                                    "question_key": "m3_persistence",
                                    "answer": right + wrong})
    facilitator.post(f"/facilitator/mission/{slug}/close", {})

    def earned(name):
        team = next(t for t in event.teams if t.display_name == name)
        return round(sum(r.points for r in ScoreEvent.query.filter_by(
            team_id=team.id, question_key="m3_persistence").all()), 2)

    assert earned("Team 1") > earned("Team 2")
    assert earned("Team 2") == 0.0
