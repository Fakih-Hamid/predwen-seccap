import pathlib
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


def code_only(html):
    return re.sub(r"/\*.*?\*/", " ", html, flags=re.S)


def readable(html):
    return re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), html)


def state(p):
    return p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()


def test_the_review_reads_the_server_not_this_browser(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "reviewBeforeLock(r.data.answers || {}, r.data.progress)" in html
    handler = html.split("lockButton.addEventListener", 1)[1]
    assert "/api/mission-state" in handler.split("});", 1)[0], (
        "the review is built without asking the server first")


def test_the_lock_call_only_happens_from_inside_the_review(app, event,
                                                           facilitator):
    """One path to the irreversible step."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = code_only(page(p))

    assert html.count("'/api/lock'") == 1
    before = html.split("'/api/lock'", 1)[0]
    assert "go.addEventListener" in before
    assert "confirm(" not in html, "the browser confirm() is still there"


def test_a_teammate_s_work_shows_up_in_the_review(app, event, facilitator):
    open_mission(facilitator)
    kenji = join(app, event, "Team 1", "kenji")
    aoi = join(app, event, "Team 1", "aoi")
    kenji.post("/api/submission", {"mission_slug": SLUG,
                                   "question_key": "m1_key_id",
                                   "answer": "SR-REL-2019"})

    answers = state(aoi)["answers"]
    assert answers["m1_key_id"]["answer"] == "SR-REL-2019"
    assert state(aoi)["progress"]["answered"] == 1


def test_locking_with_gaps_is_still_allowed(app, event, facilitator):
    """The screen warns. The server does not refuse."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    r = p.post("/api/lock", {"mission_slug": SLUG})
    assert r.get_json().get("ok"), r.get_json()
    assert state(p)["team_locked"] is True


def test_the_wording_says_it_is_a_warning_not_a_rule(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "You may still lock the submission" in html
    assert "You may still lock the submission" in html


def test_nothing_in_the_review_is_disabled(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeLock", 1)[1].split(
        "const lockButton", 1)[0]

    answers = review.split("const agreed = el(", 1)[0]
    for gate in ("disabled = true", "setAttribute('disabled'", "aria-disabled"):
        assert gate not in answers, gate

    assert "agreedBox.checked" in review


def test_every_state_is_words_and_a_mark_never_colour_alone(app, event,
                                                            facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeLock", 1)[1]

    for branch in ("mark = '—'; words =", "mark = '!'; words =",
                   "mark = '✓'; words ="):
        assert branch in review, branch
    assert "el('span', 'rmark', mark)" in review
    assert "el('span', null, words)" in review


def test_the_three_gaps_are_told_apart(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for key in ("unanswered:", "no_evidence:", "ok:", "answered_only:"):
        assert key in html, key


def test_a_question_that_never_wanted_evidence_is_not_marked_short(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "r.wantsEvidence && !r.hasEvidence" in html
    assert "wantsEvidence: !!q.querySelector('[data-ev]')" in html


def test_it_is_a_real_dialog(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "openModal(function (box, closeReview)" in html
    helper = (pathlib.Path(__file__).resolve().parents[1]
              / "app" / "static" / "seccap.js").read_text(encoding="utf-8")
    block = helper.split("window.openModal =", 1)[1]
    assert "setAttribute('role', 'dialog')" in block
    assert "setAttribute('aria-modal', 'true')" in block
    assert "setAttribute('aria-labelledby', heading.id)" in block


def test_escape_closes_it_and_tab_stays_inside(app, event, facilitator):
    helper = (pathlib.Path(__file__).resolve().parents[1]
              / "app" / "static" / "seccap.js").read_text(encoding="utf-8")
    block = helper.split("window.openModal =", 1)[1]

    assert "e.key === 'Escape'" in block
    assert "e.key !== 'Tab'" in block
    assert "e.preventDefault()" in block
    assert "last.focus()" in block and "first.focus()" in block


def test_focus_starts_on_going_back_and_returns_to_whatever_opened_it(
        app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeLock", 1)[1]

    assert "el('button', 'btn primary', REVIEW.back)" in review
    assert "focus: '.btn.primary'" in review
    assert review.index("row.appendChild(cancel)") < review.index("row.appendChild(go)")

    helper = (pathlib.Path(__file__).resolve().parents[1]
              / "app" / "static" / "seccap.js").read_text(encoding="utf-8")
    block = helper.split("window.openModal =", 1)[1]
    assert "const opener = document.activeElement;" in block
    assert "if (opener && opener.focus) opener.focus();" in block


def test_each_row_can_send_the_team_to_its_own_question(app, event,
                                                        facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeLock", 1)[1]

    assert "document.getElementById('q-' + r.key)" in review
    assert "target.focus();" in review
    assert "go.setAttribute('aria-label', REVIEW.edit + ' Q' + r.n" in review


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Review before locking"),
    ("ja", "ロック前の確認"),
    ("en", "Go back and keep working"),
    ("ja", "戻って作業を続ける"),
    ("en", "Ask the facilitator"),
    ("ja", "ファシリテーターに申し出てください"),
])
def test_the_review_speaks_both_languages(app, event, facilitator, lang,
                                          fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in readable(page(p))


def test_the_consequence_is_stated_and_names_the_way_out(app, event,
                                                         facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "the team cannot change these answers" in html
    assert "Ask the facilitator" in html
