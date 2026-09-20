import re

import pytest


def console(facilitator):
    res = facilitator.get("/facilitator/")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_close_is_not_wired_straight_to_the_endpoint(facilitator, event):
    html = console(facilitator)
    assert "[['open', 'open'], ['pause', 'pause'], ['close', 'close']]" not in html
    assert "confirmClose(m)" in html


def test_close_is_visually_distinct_from_pause(facilitator, event):
    html = console(facilitator)
    assert "el('button', 'btn sm danger', CLOSE_LABEL)" in html
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "app", "static", "seccap.css"), encoding="utf-8").read()
    rule = re.search(r"\.btn\.danger\s*\{([^}]*)\}", css)
    assert rule, "there is no .btn.danger rule"
    assert "border: 0.125rem solid var(--danger)" in rule.group(1)
    assert re.search(r"\.btn\.warn\s*\{[^}]*var\(--amber\)|\.btn\.warn\s*\{[^}]*242, 187, 69", css)


def test_the_dialog_is_accessible(facilitator, event):
    html = console(facilitator)
    for needed in ("role', 'dialog'", "aria-modal", "aria-labelledby",
                   "e.key === 'Escape'", "e.key === 'Tab'"):
        assert needed in html, needed
    assert 'data-close="' in html or "'data-close'" in html
    assert "CSS.escape(m.slug)" in html


def dialog_strings(facilitator, lang):
    import json

    with facilitator.client.session_transaction() as sess:
        sess["lang"] = lang
    html = console(facilitator)
    blob = re.search(r"const CLOSE_T = (\{.*?\});", html, re.S)
    assert blob, "the dialog's strings are no longer passed to the page"
    return json.loads(blob.group(1))


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Not locked yet"),
    ("ja", "まだロックしていないチーム"),
])
def test_the_dialog_names_the_teams_that_are_not_ready(facilitator, event, lang,
                                                       fragment):
    strings = dialog_strings(facilitator, lang)
    assert fragment in strings["not_ready"]
    assert "{teams}" in strings["not_ready"], "the team names are never filled in"


@pytest.mark.parametrize("lang,fragment", [
    ("en", "locked their submission"),
    ("ja", "提出内容をロック済み"),
])
def test_the_dialog_counts_who_is_ready(facilitator, event, lang, fragment):
    strings = dialog_strings(facilitator, lang)
    assert fragment in strings["ready"]
    assert "{n}" in strings["ready"] and "{total}" in strings["ready"]


@pytest.mark.parametrize("lang,consequence,announce", [
    ("en", "scores that work", "Announce the closure to the room first"),
    ("ja", "その時点の内容でロックされ、採点されます", "口頭で予告"),
])
def test_the_dialog_states_the_consequence_and_the_announcement(facilitator, event,
                                                                lang, consequence,
                                                                announce):
    strings = dialog_strings(facilitator, lang)
    assert consequence in strings["consequence"]
    assert announce in strings["announce"]
    assert "{mission}" in strings["title"]


def test_cancel_is_the_dominant_action_when_teams_are_not_ready(facilitator, event):
    """The common reason to hit this by accident is "I have not warned them"."""
    html = console(facilitator)
    assert "unlocked.length ? 'btn primary' : 'btn ghost'" in html
    assert "(unlocked.length ? cancel : go).focus()" in html


def test_focus_survives_the_five_second_repaint(facilitator, event):
    """The console rebuilds its cards every poll; focus used to fall to body."""
    html = console(facilitator)
    assert "function keepingFocus(" in html
    assert "function focusKeyOf(" in html
    assert "keepingFocus(function () {" in html


def test_closing_still_works_and_still_auto_locks(app, event, facilitator):
    """The confirmation is interface only. The rule is unchanged."""
    from app.models import Mission, TeamMission

    from .conftest import join

    slug = "digital-footprint"
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": slug, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})

    facilitator.post(f"/facilitator/mission/{slug}/close", {})
    assert Mission.query.filter_by(session_id=event.id, slug=slug).one().state == "closed"
    tm = TeamMission.query.filter_by(mission_slug=slug).all()
    assert tm and all(t.submitted_at is not None for t in tm), (
        "closing must still auto-lock the teams that had not locked")
    assert p.post("/api/observation",
                  {"mission_slug": slug, "text": "after"}).status_code == 409
