import re

import pytest

from .conftest import join

SLUG = "digital-footprint"
PARTICIPANT_PAGES = ("/team", "/briefing", "/mission/" + SLUG,
                     "/evidence", "/collective-intel", "/final",
                     "/scoreboard", "/artifacts/github-account")


def speak(client, lang):
    """Language lives in the session, not a cookie."""
    with client.session_transaction() as sess:
        sess["lang"] = lang


def fields_without_a_name(html):
    from html.parser import HTMLParser

    class Scan(HTMLParser):
        def __init__(self):
            super().__init__()
            self.open_labels = 0
            self.label_for = set()
            self.unnamed = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "label":
                self.open_labels += 1
                if a.get("for"):
                    self.label_for.add(a["for"])
            elif tag in ("input", "textarea", "select"):
                if a.get("type") in ("hidden", "submit", "button"):
                    return
                named = (a.get("aria-label") or a.get("aria-labelledby")
                         or self.open_labels > 0
                         or (a.get("id") and a["id"] in self.label_for))
                if not named:
                    self.unnamed.append(
                        tag + " " + " ".join(k for k in a
                                             if k.startswith("data-") or k == "id"))

        def handle_endtag(self, tag):
            if tag == "label" and self.open_labels:
                self.open_labels -= 1

    scan = Scan()
    scan.feed(html)
    second = Scan()
    second.label_for = scan.label_for
    second.feed(html)
    return second.unnamed


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_every_participant_form_control_has_an_accessible_name(app, event,
                                                               facilitator, lang):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    facilitator.post("/facilitator/session/state",
                     {"final_open": True, "collective_open": True,
                      "scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    for path in PARTICIPANT_PAGES:
        res = p.get(path)
        assert res.status_code == 200, (path, res.status_code)
        unnamed = fields_without_a_name(res.get_data(as_text=True))
        assert not unnamed, f"{path} [{lang}] has unnamed controls: {unnamed}"


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_every_page_has_a_live_region_for_status_messages(app, event, facilitator,
                                                          lang):
    """WCAG 4.1.3. The region must exist before the text is written into it."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    for path in PARTICIPANT_PAGES:
        html = p.get(path).get_data(as_text=True)
        assert 'id="toast"' in html, path
        assert 'role="status"' in html, f"{path} [{lang}] has no status region"
        assert 'aria-live="polite"' in html, path


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_every_page_starts_with_a_skip_link(app, event, facilitator, lang):
    """WCAG 2.4.1, over a header that repeats on every screen."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    for path in PARTICIPANT_PAGES:
        html = p.get(path).get_data(as_text=True)
        body = html[html.index("<body"):]
        first = re.search(r'<a[^>]*class="skip"[^>]*href="#main"', body)
        assert first, f"{path} [{lang}] has no skip link"
        assert first.start() < body.index("<header"), path
        assert 'id="main"' in html, path


def test_the_saved_state_is_not_conveyed_by_colour_alone(app, event, facilitator):
    """WCAG 1.4.1. The tick and the word carry it; colour decorates it."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert "data-saved" in html and "data-savedtext" in html
    assert "✓" in html
    assert "markSaved(q, saved.updated_at, saved.updated_by)" in html
    assert "written by {who}" in html


def test_repeated_controls_have_distinct_accessible_names(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)

    assert "data-take=" not in html
    labels = re.findall(r'data-reveal[^>]*aria-label="([^"]+)"', html)
    assert len(labels) >= 7, labels
    assert len(set(labels)) == len(labels), labels

    moves = re.findall(r'data-move="(?:up|down)"\s+aria-label="([^"]+)"', html)
    if moves:                                  # only missions with an ordering
        assert len(set(moves)) == len(moves), moves


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_answer_and_evidence_fields_name_their_question(app, event,
                                                            facilitator, lang):
    """'Excerpt' seven times down a page says nothing about which excerpt."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    answers = re.findall(r'data-answer[^>]*aria-label="([^"]+)"', html)
    assert answers, "no named answer fields"
    assert len(set(answers)) == len(answers), answers
    excerpts = re.findall(r'data-evexcerpt[^>]*aria-label="([^"]+)"', html)
    assert len(set(excerpts)) == len(excerpts), excerpts


def test_target_size_rules_exist_for_the_small_controls():
    """WCAG 2.2 target size (minimum), 24 CSS px."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "app", "static", "seccap.css"),
               encoding="utf-8").read()
    for selector in (".btn.sm", ".orderitem .mv .btn.sm", ".seg button",
                     ".hintrow .btn.sm", ".langbox button"):
        found = re.search(re.escape(selector) + r"\s*\{[^}]*min-(?:height|width):\s*1\.5rem",
                          css)
        assert found, f"{selector} has no 24px minimum"
