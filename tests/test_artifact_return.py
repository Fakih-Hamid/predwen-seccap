import pathlib
import re

import pytest

from .conftest import join

SLUG = "digital-footprint"
ARTIFACT = "github-account"          # a launch point of mission 1
CSS = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "seccap.css"


def speak(client, lang):
    """Language lives in the session, not a cookie."""
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def artifact_page(p):
    r = p.get(f"/artifacts/{ARTIFACT}")
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def backbar_of(html):
    assert 'class="backbar"' in html, "the return is not in the sticky bar"
    bar = html.split('class="backbar"', 1)[1].split("</div>", 1)[0]
    return re.sub(r"<[^>]+>", " ", bar)


def test_the_way_back_names_the_mission_it_returns_to(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = artifact_page(p)

    assert f'href="/mission/{SLUG}"' in html, "the bar does not link to the mission"
    assert "Back to the mission" in backbar_of(html)


def test_the_bar_carries_the_mission_title(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    words = backbar_of(artifact_page(p)).replace("←", " ").split()
    assert len(words) > 4, f"the bar carries no mission title: {words!r}"
    assert SLUG not in " ".join(words), "the bar shows the slug, not the title"


def test_every_question_is_addressable(app, event, facilitator):
    """`#q-<key>` has to exist on the mission page for the capture to aim at."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    pairs = re.findall(r'class="q" id="q-([^"]+)" tabindex="-1" data-key="([^"]+)"',
                       html)
    assert pairs, "no question carries an id"
    for anchor, key in pairs:
        assert anchor == key, (anchor, key)


def test_the_anchor_can_take_focus(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert re.search(r'class="q" id="q-[^"]+" tabindex="-1"', html)
    assert "focusRequestedQuestion" in html
    assert "q.focus()" in html


def test_nothing_of_the_capture_is_left_on_the_artifact_page(app, event,
                                                             facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = code_only(artifact_page(p))

    for gone in ("captarget", "capstate", "capsaved", "seccap.capture",
                 "Bring this into your notes"):
        assert gone not in html, gone


def code_only(html):
    without_js_comments = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return re.sub(r"\{#.*?#\}", "", without_js_comments, flags=re.S)


def test_nothing_of_the_capture_is_left_on_the_mission_page(app, event,
                                                            facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = code_only(p.get(f"/mission/{SLUG}").get_data(as_text=True))

    for gone in ("applyCapture", "seccap.capture", "capnote",
                 "captureArtifact", "data-capture"):
        assert gone not in html, gone


def test_the_fragment_still_lands_on_its_question(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "focusRequestedQuestion();" in html
    block = html.split("function focusRequestedQuestion", 1)[1]
    assert "showStepContaining(q)" in block, "it lands on a hidden step"
    assert "q.focus();" in block


def test_the_evidence_fields_are_where_the_capture_used_to_fill_them(
        app, event, facilitator):
    """Removing the shortcut is only safe if the destination still works."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")

    r = p.post("/api/evidence", {"mission_slug": SLUG,
                                "question_key": "m1_key_id",
                                "artifact_id": ARTIFACT,
                                "source_url": "https://github.com/sora-dev77",
                                "excerpt": "read it here"})
    assert r.get_json().get("ok"), r.get_json()

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    for field in ("data-evart", "data-evurl", "data-evexcerpt"):
        assert field in html, field
def test_the_return_is_reachable_from_anywhere_on_the_page():
    bar = CSS.read_text(encoding="utf-8").split(".backbar {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in bar
    assert "top: 0" in bar
