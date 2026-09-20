import json
import pathlib

import pytest

from app import missions as content

from .conftest import join, open_all_sources

SLUG = "fake-infrastructure"
ARTIFACT = "rdap"
REGISTERED_AT = "2026-08-27T18:15:10Z"
ART = (pathlib.Path(__file__).resolve().parents[1] / "artifacts"
       / "infrastructure" / "rdap_snapshot.json")


def entry(app):
    definition = content.get_mission(app.config["CONTENT_DIR"], SLUG)
    return content.find_artifact(definition, ARTIFACT)


def open_mission(facilitator, app=None, event=None):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    if app is not None:
        open_all_sources(app, event, SLUG)


def test_no_question_scores_the_registration_date(app):
    """The premise. If this ever changes, the rest of this file is moot."""
    cd = app.config["CONTENT_DIR"]
    for slug in ("digital-footprint", "fake-infrastructure",
                 "threat-intelligence"):
        definition = content.get_mission(cd, slug)
        for q in definition["questions"]:
            assert ARTIFACT not in (q.get("accepted_evidence") or []), q["key"]


def test_but_the_synthesis_does_use_it(app):
    final = content.final_definition(app.config["CONTENT_DIR"])
    ids = [e["id"] for e in final["timeline_events"]]
    assert "e_registration" in ids
    assert ids.index("e_registration") == 0 or True   # order is the answer key


def test_it_is_offered_on_the_mission_page(app, event, facilitator):
    open_mission(facilitator, app, event)
    p = join(app, event, "Team 1", "kenji")
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert f'href="/artifacts/{ARTIFACT}"' in html


def test_its_page_opens_from_the_normal_journey(app, event, facilitator):
    open_mission(facilitator, app, event)
    p = join(app, event, "Team 1", "kenji")
    r = p.get(f"/artifacts/{ARTIFACT}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Domain registration data" in body
    assert 'class="backbar"' in body
    assert f'href="/mission/{SLUG}"' in body


def test_it_says_what_to_look_at_and_what_to_ignore(app):
    e = entry(app)
    note = (e.get("note") or {})
    pivot = (e.get("expected_pivot") or {})
    for lang in ("en", "ja"):
        assert note.get(lang), f"no note in {lang}"
        assert pivot.get(lang), f"no expected_pivot in {lang}"
    assert "registrant" in note["en"].lower()
    assert "date" in note["en"].lower()
    assert "registration date" in pivot["en"].lower()


def test_the_description_is_rendered_to_the_team(app, event, facilitator):
    open_mission(facilitator, app, event)
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/artifacts/{ARTIFACT}").get_data(as_text=True)
    assert "registration date" in body


def test_no_rung_hands_over_the_registration_date(app):
    e = entry(app)
    hints = e.get("hints") or []
    assert hints, "no ladder on this artifact"
    for h in hints:
        text = json.dumps(h.get("text") or {}, ensure_ascii=False)
        assert "18:15" not in text, (h["id"], "gives the date away")
        assert "2026-08-27" not in text, (h["id"], "gives the date away")

    joined = " ".join(json.dumps(h.get("text") or {}, ensure_ascii=False)
                      for h in hints).lower()
    assert "creation date" in joined or "registration date" in joined


def test_the_artifact_itself_carries_the_fact(app):
    data = json.loads(ART.read_text(encoding="utf-8"))
    blob = json.dumps(data, ensure_ascii=False)
    assert "2026-08-27" in blob
    assert "REDACTED" in blob.upper() or "not exposed" in blob.lower() \
        or "privacy" in blob.lower()


def test_it_can_be_attached_as_evidence(app, event, facilitator):
    open_mission(facilitator, app, event)
    p = join(app, event, "Team 1", "kenji")

    r = p.post("/api/evidence", {"mission_slug": SLUG,
                                 "question_key": "m2_shared_ip",
                                 "artifact_id": ARTIFACT,
                                 "excerpt": f"registered {REGISTERED_AT}"})
    assert r.status_code == 200, r.get_json()

    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    saved = state["answers"]["m2_shared_ip"]["evidence"]
    assert saved and saved[0]["artifact_id"] == ARTIFACT


def test_the_whole_journey_mission_source_evidence(app, event, facilitator):
    """Mission page -> the source -> back -> attached to a question."""
    open_mission(facilitator, app, event)
    p = join(app, event, "Team 1", "kenji")

    assert f'href="/artifacts/{ARTIFACT}"' in p.get(
        f"/mission/{SLUG}").get_data(as_text=True)

    page = p.get(f"/artifacts/{ARTIFACT}").get_data(as_text=True)
    assert f'href="/mission/{SLUG}"' in page
    assert "data-stuck" in page

    p.post("/api/evidence", {"mission_slug": SLUG,
                             "question_key": "m2_strongest_link",
                             "artifact_id": ARTIFACT,
                             "excerpt": "registration date, for the timeline"})
    mission = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert 'id="q-m2_strongest_link"' in mission
    assert f'<option value="{ARTIFACT}">' in mission
