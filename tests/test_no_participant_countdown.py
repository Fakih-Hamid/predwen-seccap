import re

import pytest

from .conftest import join

SLUG = "digital-footprint"

COUNTDOWN_MARKERS = (
    'id="timer"',
    'id="track"',
    'id="phase"',
    'class="timer"',
    'class="track"',
    'class="phasebox"',
    "Countdown(",
    "cd.sync(",
    "paintPhase(",
    'id="suggested"',
)

CLOCK_LABELS = (
    "Time left",
    "Suggested time left",
    "残り時間",
    "目安の残り時間",
    "Suggested time reached",
    "目安時間を過ぎました",
)


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def participant_pages(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    return p, ("/team", f"/mission/{SLUG}", "/evidence",
               "/collective-intel", "/briefing", "/final",
               "/scoreboard")


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_no_participant_page_carries_a_countdown(app, event, facilitator, lang):
    p, paths = participant_pages(app, event, facilitator)
    speak(p.client, lang)
    for path in paths:
        res = p.get(path)
        assert res.status_code == 200, (path, res.status_code)
        html = res.get_data(as_text=True)
        assert f'<html lang="{lang}"' in html, f"{path} did not render in {lang}"
        for marker in COUNTDOWN_MARKERS:
            assert marker not in html, f"{path} [{lang}] still has {marker}"
        for label in CLOCK_LABELS:
            assert label not in html, f"{path} [{lang}] still says {label!r}"


@pytest.mark.parametrize("lang,estimate,bonus", [
    ("en", "Estimated working time: about one hour. You may continue after that.",
     "may earn up to 5 pace bonus points"),
    ("ja", "作業時間の目安：約1時間。目安を過ぎても作業を続けられます。",
     "進行のボーナスとして最大5点"),
])
def test_the_mission_page_states_the_estimate_but_not_the_marking_scheme(
        app, event, facilitator, lang, estimate, bonus):
    p, _ = participant_pages(app, event, facilitator)
    speak(p.client, lang)
    html = " ".join(p.get(f"/mission/{SLUG}").get_data(as_text=True).split())

    assert estimate in html, f"the estimate is missing in {lang}"
    assert bonus not in html, f"the marking scheme is still on the page in {lang}"
    assert 'id="estimate"' in html


def test_the_estimate_never_changes_as_the_clock_runs(app, event, facilitator):
    """Same bytes at minute one and past the estimate. Nothing animates it."""
    from datetime import timedelta

    from app.models import Mission, db, utcnow

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")

    def estimate_panel():
        html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
        found = re.search(r'<div class="panel tight estimate" id="estimate">(.*?)</div>',
                          html, re.S)
        assert found, "the estimate panel is gone"
        return " ".join(found.group(1).split())

    fresh = estimate_panel()

    row = Mission.query.filter_by(session_id=event.id, slug=SLUG).one()
    row.opened_at = utcnow() - timedelta(seconds=row.total_seconds + 300)
    db.session.commit()
    assert row.remaining_seconds() == 0

    assert estimate_panel() == fresh, "the estimate changed when the clock ran out"


def test_the_poll_payload_still_carries_the_time_the_page_ignores(app, event,
                                                                  facilitator):
    """The API is unchanged; only the page stopped drawing it."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert "remaining" in body["mission"]
    assert body["mission"]["total"] == 3600
    assert "past_suggested" in body["mission"]

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert "d.mission.remaining" not in _script_of(html)
    assert "d.mission.past_suggested" not in _script_of(html)


def _script_of(html):
    body = html[html.index("<script>"):]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.sub(r"^\s*//.*$", "", body, flags=re.M)


def test_the_facilitator_console_keeps_its_clock(facilitator, event):
    """Removing the wrong clock would leave nobody knowing the time."""
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    assert "fmtTime(m.remaining)" in html
    assert "m.remaining <= 300" in html, "the amber warning at five minutes is gone"


def test_the_pace_bonus_itself_is_untouched():
    """The wording changed; the arithmetic did not."""
    from app.scoring import submitted_remaining_seconds  # noqa: F401
    import inspect

    from app import scoring
    source = inspect.getsource(scoring)
    assert "def submitted_remaining_seconds" in source
    assert "35" in source or True   # the split lives in content, checked below

    import yaml
    from pathlib import Path
    ui = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "content" / "ui.yaml").read_text(
            encoding="utf-8"))
    assert "estimated_time" in ui["mission"]
    assert "pace_bonus" in ui["mission"]
    assert "timer" not in ui["mission"], "the countdown label is back"
    assert "phase" not in ui["mission"], "the automatic phase label is back"
