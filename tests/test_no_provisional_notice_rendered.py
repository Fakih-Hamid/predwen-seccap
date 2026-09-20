import re

import pytest

from .conftest import join

FORBIDDEN = (
    "Japanese text is provisional",
    "awaits native review",
    "provisional and awaits",
    "native review",
    "翻訳は暫定",
    "暫定的な訳",
    "ネイティブによる確認",
)

PARTICIPANT = ("/team", "/briefing", "/mission/digital-footprint",
               "/evidence", "/collective-intel", "/final",
               "/scoreboard", "/artifacts/github-account", "/presentation")
FACILITATOR = ("/facilitator/", "/scoreboard?projector")


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def strip_comments(html):
    return re.sub(r"<!--.*?-->", " ", html, flags=re.S)


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_no_participant_page_carries_it(app, event, facilitator, lang):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    facilitator.post("/facilitator/session/state",
                     {"final_open": True, "collective_open": True,
                      "scoreboard_visible": True})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)

    for path in PARTICIPANT:
        res = p.get(path)
        assert res.status_code == 200, (path, res.status_code)
        body = strip_comments(res.get_data(as_text=True))
        for phrase in FORBIDDEN:
            assert phrase not in body, f"{path} [{lang}] says {phrase!r}"


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_no_facilitator_page_carries_it(app, event, facilitator, lang):
    speak(facilitator.client, lang)
    for path in FACILITATOR:
        body = strip_comments(facilitator.get(path).get_data(as_text=True))
        for phrase in FORBIDDEN:
            assert phrase not in body, f"{path} [{lang}] says {phrase!r}"


def test_the_key_itself_is_gone(app):
    from app import ui
    assert ui.t(app.config["CONTENT_DIR"], "app.provisional_note",
                "ja").startswith("⟦"), "the string is back in the content"
