import json

import pytest

from .conftest import join

SLUG = "digital-footprint"


def status(facilitator):
    body = facilitator.get("/facilitator/api/progress").get_json()
    assert "status" in body, "the console has no status payload"
    return body["status"]


def test_it_names_the_active_mission_and_its_clock(app, event, facilitator):
    before = status(facilitator)
    assert before["active_slug"] is None
    assert before["elapsed"] is None

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    now = status(facilitator)
    assert now["active_slug"] == SLUG
    assert now["active_mission"] and now["active_mission"] != SLUG, (
        "the strip should show the mission's title, not its slug")
    assert now["elapsed"] is not None and now["remaining"] is not None


def test_it_counts_locked_teams_out_of_all_of_them(app, event, facilitator):
    from app.state import DEFAULT_TEAM_COUNT

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    assert status(facilitator)["teams_total"] == DEFAULT_TEAM_COUNT
    assert status(facilitator)["teams_locked"] == 0

    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/lock", {"mission_slug": SLUG})
    assert status(facilitator)["teams_locked"] == 1


def test_it_names_the_teams_that_need_attention(app, event, facilitator):
    """Named, not counted: "two teams need you" is not actionable."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    join(app, event, "Team 1", "kenji")          # one team has somebody online
    flagged = status(facilitator)["attention"]
    names = {a["name"] for a in flagged}
    from app.state import DEFAULT_TEAM_COUNT

    assert "Team 1" not in names, flagged
    assert len(names) == DEFAULT_TEAM_COUNT - 1, flagged   # the others are empty
    assert all(a["why"] == "offline" for a in flagged), flagged


def test_it_no_longer_reports_an_offline_mode(app, event, facilitator):
    assert "offline_mode" not in status(facilitator)


def test_it_reports_the_last_export_and_only_after_one_happens(app, event,
                                                               facilitator):
    assert status(facilitator)["last_export"] is None

    facilitator.get("/facilitator/export/results.csv")
    first = status(facilitator)["last_export"]
    assert first and first["kind"] == "scores", first

    facilitator.get("/facilitator/export/session.json")
    second = status(facilitator)["last_export"]
    assert second["kind"] == "session", second


def test_the_export_row_is_committed_not_just_staged(app, event, facilitator):
    from app.models import AuditEvent

    facilitator.get("/facilitator/export/submissions.csv")
    rows = AuditEvent.query.filter_by(session_id=event.id, action="export").all()
    assert rows, "the export was not recorded"
    assert rows[-1].target == "submissions"


def test_it_does_not_claim_to_know_about_the_server_backup(facilitator, event):
    """The app runs in a container and cannot see the host filesystem."""
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    assert "backup.sh" in html
    for false_claim in ("Backed up", "backup complete", "バックアップ済み"):
        assert false_claim not in html, false_claim


@pytest.mark.parametrize("lang,key,fragment", [
    ("en", "st_next_close_wait", "Announce before you close"),
    ("ja", "st_next_close_wait", "口頭で予告"),
    ("en", "backup_note", "verified outside the app"),
    ("ja", "backup_note", "アプリ外で確認"),
])
def test_the_strip_speaks_both_languages(facilitator, event, lang, key, fragment):
    with facilitator.client.session_transaction() as sess:
        sess["lang"] = lang
    html = facilitator.get("/facilitator/").get_data(as_text=True)
    import re
    blob = re.search(r"const ST = (\{.*?\});", html, re.S)
    strings = json.loads(blob.group(1)) if blob else {}
    haystack = strings.get(key, "") + html
    assert fragment in haystack or fragment in json.dumps(strings, ensure_ascii=False)
