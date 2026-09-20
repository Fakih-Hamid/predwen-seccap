import pathlib
import re

import pytest

from .conftest import join

SLUG = "digital-footprint"
JS = (pathlib.Path(__file__).resolve().parents[1]
      / "app" / "static" / "seccap.js")


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator, slug=SLUG):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})


def page(p, slug=SLUG):
    return p.get(f"/mission/{slug}").get_data(as_text=True)


def helper():
    return JS.read_text(encoding="utf-8").split("window.openModal =", 1)[1] \
        .split("/* ---", 1)[0]


def test_there_is_exactly_one_focus_trap(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    assert "el('div', 'modalback')" not in html
    assert html.count("document.addEventListener('keydown'") == 0
    assert html.count("openModal(") == 5


def test_the_helper_returns_focus_to_whatever_opened_the_dialog():
    block = helper()
    assert "const opener = document.activeElement;" in block
    assert "if (opener && opener.focus) opener.focus();" in block


def test_the_helper_labels_the_dialog_from_its_own_heading():
    block = helper()
    assert "box.querySelector('h2, h3')" in block
    assert "setAttribute('aria-labelledby', heading.id)" in block


def test_escape_and_the_backdrop_both_close_it():
    block = helper()
    assert "e.key === 'Escape'" in block
    assert "if (e.target === back) close();" in block


def test_the_page_behind_does_not_scroll():
    block = helper()
    assert "classList.add('modal-open')" in block
    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "static" / "seccap.css").read_text(encoding="utf-8")
    assert "body.modal-open { overflow: hidden; }" in css


def test_the_walkthrough_has_one_card_per_step(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    cards = re.findall(r'<span data-card\s+data-step-title="([^"]+)"', html)
    assert len(cards) == 4, cards
    assert len(set(cards)) == 4, "two cards describe the same step"


def test_it_is_shown_once_per_browser_and_not_once_per_mission(app, event,
                                                               facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    assert "const TOUR_SEEN = 'seccap.tour';" in html
    assert "localStorage.getItem(TOUR_SEEN)" in html
    assert "'seccap.tour.' + SLUG" not in html


def test_it_can_be_brought_back(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))

    assert 'id="retour"' in html
    assert "document.getElementById('retour').addEventListener('click', showTour)" in html


def test_it_is_skippable_from_every_card(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    block = html.split("function showTour", 1)[1].split("document.getElementById('retour')", 1)[0]

    assert "TOUR.dataset.skip" in block
    assert "skip.addEventListener('click'" in block
    assert block.count("remember()") >= 2


def test_a_private_window_does_not_break_it(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    assert "} catch (e) { /* private window: the tour is optional, so skip it */ }" in html


@pytest.mark.parametrize("lang,fragment", [
    ("en", "How this mission works"),
    ("ja", "このミッションの進め方"),
    ("en", "Use the question hints when needed"),
    ("ja", "詰まったら設問のヒントを使ってください"),
])
def test_the_walkthrough_speaks_both_languages(app, event, facilitator, lang,
                                               fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in page(p)


def test_the_completion_note_fires_only_on_real_completion(app, event,
                                                           facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    block = html.split("function maybeCelebrate", 1)[1].split("function locked", 1)[0]

    assert "p.answered < p.questions" in block
    assert "p.with_evidence < p.evidence_wanted" in block
    assert "locked()" in block


def test_it_cannot_open_twice(app, event, facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    block = html.split("function maybeCelebrate", 1)[1].split("function locked", 1)[0]

    assert "if (celebrated" in block
    assert "celebrated = true;" in block
    assert "sessionStorage.getItem(DONE_KEY)" in block
    assert "const DONE_KEY = 'seccap.done.' + SLUG;" in html


def test_it_offers_the_next_action_and_a_way_to_ignore_it(app, event,
                                                          facilitator):
    open_mission(facilitator)
    html = page(join(app, event, "Team 1", "kenji"))
    block = html.split("function maybeCelebrate", 1)[1].split("function locked", 1)[0]

    assert "TOUR.dataset.doneReview" in block
    assert "TOUR.dataset.doneLater" in block
    assert "move(STEPS.indexOf('review'), true)" in block
    assert "/api/lock" not in block


@pytest.mark.parametrize("lang,fragment", [
    ("en", "Mission complete"),
    ("ja", "すべての設問に回答しました"),
    ("en", "or keep working"),
    ("ja", "そのまま作業を続けることもできます"),
])
def test_the_completion_note_speaks_both_languages(app, event, facilitator,
                                                   lang, fragment):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    assert fragment in page(p)


def test_no_dialog_text_travels_through_tojson(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, "ja")
    html = page(p)

    block = html.split('id="tourdata"', 1)[1].split("</div>", 1)[0]
    assert "このミッションの進め方" in block
    assert "すべての設問に回答しました" in block
    assert "\\u" not in block, "a dialog string was escaped by tojson"
