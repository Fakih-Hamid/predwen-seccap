import re

import pytest

from .conftest import join

FIELDS = ("verdict", "confirmed_facts", "inferences", "unknowns", "limitations")


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_final(facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})


def page(p):
    r = p.get("/final")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def readable(html):
    return re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), html)


def test_the_one_line_confirm_is_gone(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    code = re.sub(r"/\*.*?\*/", " ", html, flags=re.S)
    assert "confirm(CONFIRM)" not in code
    assert "reviewBeforeFinalLock" in code


def test_the_lock_goes_through_the_review(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    handler = html.split("getElementById('lockfinal').addEventListener", 1)[1]
    assert "reviewBeforeFinalLock(doLock)" in handler.split("});", 1)[0]
    assert html.count("'/api/final/lock'") == 1
    assert "async function doLock()" in html


def test_it_lists_every_written_field_and_both_orderings(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for field in FIELDS:
        assert f"id: '{field}'" in html, field
    assert "id: 'timeline'" in html
    assert "id: 'response'" in html


def test_it_marks_blank_and_filled_with_words(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    for key in ('"filled"', '"blank"', '"ordered"', '"not_ordered"'):
        assert key in html, key
    assert "el('span', 'rmark', r.text ? '✓' : '—')" in html
    assert "r.text ? RV.filled : RV.blank" in html


def test_every_row_offers_a_way_to_its_field(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    assert "function focusField(id)" in html
    assert "target.focus();" in html
    assert "RV.go + ' — ' + r.label" in html


def test_it_states_the_consequence(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)
    assert "the synthesis cannot be changed" in html
    assert "Ask the facilitator" in html


def test_it_never_refuses(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeFinalLock", 1)[1].split(
        "document.getElementById('lockfinal')", 1)[0]

    for gate in ("disabled = true", "aria-disabled", "return false"):
        assert gate not in review, gate
    assert "The items marked below are not complete" in page(p)


def test_an_empty_synthesis_can_still_be_locked(app, event, facilitator):
    """The server side of "warns, does not forbid"."""
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    r = p.post("/api/final/lock", {})
    assert r.status_code == 200
    assert r.get_json().get("ok") is True


def test_nothing_is_lost_on_the_way_to_the_lock(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = page(p)

    lock_block = html.split("async function doLock()", 1)[1]
    assert lock_block.index("'/api/final'") < lock_block.index("'/api/final/lock'")
    for field in FIELDS:
        assert f"document.getElementById('{field}').value" in lock_block


def test_it_is_a_real_dialog(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeFinalLock", 1)[1]

    assert "setAttribute('role', 'dialog')" in review
    assert "setAttribute('aria-modal', 'true')" in review
    assert "setAttribute('aria-labelledby', 'finalreviewh')" in review


def test_escape_and_cancel_both_restore_focus(app, event, facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeFinalLock", 1)[1]

    assert "e.key === 'Escape'" in review
    assert "cancel.addEventListener('click', closeReview)" in review
    assert "const opener = document.getElementById('lockfinal');" in review
    assert "opener.focus();" in review


def test_tab_stays_inside_and_going_back_takes_focus_first(app, event,
                                                           facilitator):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    review = page(p).split("function reviewBeforeFinalLock", 1)[1]

    assert "e.key === 'Tab'" in review
    assert "e.preventDefault()" in review
    assert "cancel.focus();" in review
    assert review.index("row.appendChild(cancel)") < review.index("row.appendChild(go)")


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Review before locking: the synthesis"),
    ("ja", "ロック前の確認：最終報告書"),
    ("en", "Go back and keep working"),
    ("ja", "戻って作業を続ける"),
    ("en", "the presentation is generated from it"),
    ("ja", "発表資料はこの内容から生成されます"),
])
def test_the_review_speaks_both_languages(app, event, facilitator, lang,
                                          fragment):
    open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in readable(page(p)), fragment
