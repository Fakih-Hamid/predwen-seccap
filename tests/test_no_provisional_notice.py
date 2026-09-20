import re

import pytest

from .conftest import join

BANNED = (
    "Japanese text is provisional and awaits native review.",
    "日本語は暫定版です（ネイティブ校閲前）。",
    "provisional and awaits native review",
    "ネイティブ校閲前",
    "暫定版です",
)

TRANSLATION_CLAIM = re.compile(
    r"(japanese[^.<]{0,40}(provisional|awaits? (a )?native|not (yet )?review))"
    r"|(日本語[^。<]{0,30}(暫定|校閲))",
    re.I)


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def _assert_clean(res, where):
    assert res.status_code == 200, f"{where} returned {res.status_code}"
    html = res.get_data(as_text=True)
    assert "</footer>" in html, f"{where} rendered no footer — nothing was checked"
    if where.endswith("[ja]"):
        assert '<html lang="ja"' in html, f"{where} did not render in Japanese"
    for phrase in BANNED:
        assert phrase not in html, f"{where} still shows: {phrase}"
    match = TRANSLATION_CLAIM.search(html)
    assert match is None, f"{where} still claims the Japanese is provisional: {match.group(0)!r}"


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_public_pages_carry_no_translation_notice(client, event, lang):
    speak(client, lang)
    for path in ("/", "/join"):
        _assert_clean(client.get(path), f"{path} [{lang}]")


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_participant_surfaces_carry_no_translation_notice(app, event, facilitator,
                                                              lang):
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)

    for path in ("/team",
                 "/mission/digital-footprint",
                 "/mission/fake-infrastructure",      # still locked: holding screen
                 "/evidence",
                 "/collective-intel",
                 "/briefing",
                 "/final",
                 "/presentation",
                 "/scoreboard",
                 "/artifacts/github-account"):
        _assert_clean(p.get(path), f"{path} [{lang}]")


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_facilitator_surfaces_carry_no_translation_notice(facilitator, event, lang):
    speak(facilitator.client, lang)
    for path in ("/facilitator/", "/facilitator/login"):
        _assert_clean(facilitator.get(path), f"{path} [{lang}]")


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_offline_mode_carries_no_translation_notice(app, event, facilitator, lang):
    """Fallback mode re-renders artifacts from saved copies — same footer."""
    facilitator.post("/facilitator/mission/digital-footprint/open", {})
    facilitator.post("/facilitator/flags", {"fallback_snapshots": True})
    p = join(app, event, "Team 1", "aoi")
    speak(p.client, lang)
    for path in ("/mission/digital-footprint", "/artifacts/github-account"):
        _assert_clean(p.get(path), f"{path} offline [{lang}]")


def test_the_footer_is_just_the_name_and_the_tagline(app, event, facilitator):
    """And has no dangling separator where the note used to be."""
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/team").get_data(as_text=True)
    match = re.search(r"<div>([^<]*Predwen SECCAP[^<]*)</div>\s*</footer>", html)
    assert match, "the footer line is no longer recognisable"
    line = " ".join(match.group(1).split())
    assert line.count("·") == 1, f"dangling separator: {line!r}"
    assert not line.endswith("·"), line
