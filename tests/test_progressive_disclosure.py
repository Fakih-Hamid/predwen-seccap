import re

import pytest

from .conftest import join

SLUG = "digital-footprint"


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def page(p):
    return p.get(f"/mission/{SLUG}").get_data(as_text=True)


def progress_of(p):
    return p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()["progress"]


def block(html, marker):
    """One `<details ...>` opening tag, by something inside its attributes."""
    for tag in re.findall(r"<details[^>]*>", html):
        if marker in tag:
            return tag
    raise AssertionError(f"no <details> carrying {marker!r}")


def test_the_narrative_is_open_until_the_team_has_started(app, event,
                                                          facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert " open" in block(page(p), 'id="situation"')

    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    assert " open" not in block(page(p), 'id="situation"')


def test_an_observation_counts_as_having_started_too(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/observation", {"mission_slug": SLUG, "text": "the updater"})
    assert " open" not in block(page(p), 'id="situation"')


def test_help_is_beside_each_question_and_nowhere_generic(app, event,
                                                        facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert '<div class="hintrow"' not in html
    assert "data-take=" not in html
    assert 'id="unblock"' not in html
    assert 'data-scope="mission"' not in html, "a generic ladder is still there"

    blocks = re.findall(r'<details class="qhint"[^>]*data-scope="q:([^"]+)"', html)
    assert len(blocks) == 7, blocks
    assert len(set(blocks)) == 7, "two questions share a hint block"
    assert html.count('data-total="3"') == 7
    assert len(re.findall(r'<span class="stuckrung" data-rung="', html)) == 21
    assert "Open the commit and read" not in html, "a hint body shipped with the page"


def test_no_countdown_and_no_disabled_control_reaches_the_page(app, event,
                                                               facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for block in re.findall(r'<details class="qhint".*?</details>', html, re.S):
        assert "disabled" not in block
        assert "opens in" not in block
        assert "opens_in" not in block


def test_every_question_rung_is_available_the_moment_it_is_asked_for(
        app, event, facilitator):
    from app.models import ScoreEvent, Team

    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)
    rungs = re.findall(r'data-rung="([^"]+)"', html)
    assert len(rungs) == 21

    for hint_id in rungs:
        r = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": hint_id})
        assert r.status_code == 200, (hint_id, r.status_code)
        assert r.get_json()["hint"]["text"], hint_id

    with app.app_context():
        team = Team.query.filter_by(session_id=event.id,
                                    display_name="Team 1").one()
        charged = ScoreEvent.query.filter_by(team_id=team.id, source="hint").all()
        assert all(row.points == 0 for row in charged)


def test_the_working_zone_folds_the_secondary_inputs(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert html.count("data-working") >= 7, "questions have no working zone"
    for q in re.findall(r'<div class="q"[^>]*>.*?</div>\s*</div>', html, re.S)[:1]:
        answer = q.split('<details class="working"', 1)[0]
        answer = re.sub(r'<details class="qhint".*?</details>', " ", answer, flags=re.S)
        assert "data-answer" in answer or "data-conf" in answer or "data-order" in answer


def test_the_evidence_fields_are_all_still_there(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for control in ("data-evart", "data-evurl", "data-evexcerpt", "data-reasoning"):
        assert control in html, control
    assert html.count("aria-label") >= 26

    r = p.post("/api/evidence", {"mission_slug": SLUG,
                                 "question_key": "m1_key_id",
                                 "artifact_id": "github-account",
                                 "excerpt": "read it here"})
    assert r.get_json().get("ok"), r.get_json()


def test_a_folded_zone_that_has_something_in_it_opens_itself(app, event,
                                                             facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "function showWorking(q, filled)" in html
    assert "if (filled) box.open = true;" in html
    assert "showWorking(q, hasEvidence || !!saved.reasoning);" in html


def test_saving_evidence_opens_the_zone_it_was_saved_into(app, event,
                                                          facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    saver = html.split("async function writeEvidence", 1)[1].split(
        "function save(q)", 1)[0]
    assert "showWorking(q, true);" in saver
    assert "showWorking(q, hasEvidence || !!saved.reasoning);" in html


def test_the_summary_says_whether_anything_is_behind_it(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "data-workstate" in html
    assert "nothing yet" in html
    assert "WORK_FILLED" in html and "WORK_EMPTY" in html


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Evidence and reasoning"),
    ("ja", "根拠と考え方"),
    ("en", "nothing yet"),
    ("ja", "未記入"),
])
def test_the_folds_speak_both_languages(app, event, facilitator, lang, fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in page(p)


def test_evidence_is_counted_even_before_the_answer_is_written(app, event,
                                                               facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account",
                             "excerpt": "read it here"})

    counts = progress_of(p)
    assert counts["answered"] == 0
    assert counts["with_evidence"] == 1


def test_a_closed_details_actually_hides_its_contents(app, event, facilitator):
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "static" / "seccap.css").read_text(encoding="utf-8")
    assert "details:not([open]) > *:not(summary) { display: none; }" in css


def test_nothing_is_folded_behind_a_disabled_control(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for tag in re.findall(r"<summary[^>]*>", html):
        for gate in ("disabled", "aria-disabled", "hidden"):
            assert gate not in tag, (gate, tag)
