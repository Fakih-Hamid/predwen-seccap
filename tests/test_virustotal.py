import json
import os
from pathlib import Path

from app import external, missions as content

from .conftest import join, open_all_sources, open_mission, wind_forward

SLUG = "threat-intelligence"
M2 = "fake-infrastructure"
KEY = "m3_virustotal"
ARTIFACT = "virustotal"
RUBRIC = "m3_r_virustotal_limit"
CANONICAL_HASH = "ff749df77076e1243eb3c8a3b8bb049d2e7913125532b6ab2bf0b8299ef8ea31"


def cd(app):
    return app.config["CONTENT_DIR"]


def _open_all(facilitator):
    for slug in ("digital-footprint", M2, SLUG):
        open_mission(facilitator, slug)


def _offline(facilitator, on=True):
    facilitator.post("/facilitator/session/state", {"fallback_snapshots": on})


def _m3(app):
    return content.load_missions(cd(app))[SLUG]


def _artifact(app):
    return next(a for a in _m3(app)["artifacts"] if a["id"] == ARTIFACT)


def test_the_lookup_uses_the_file_route_and_never_the_search_route(app):
    entry = external.load(cd(app))[KEY]
    assert "/gui/file/" in entry["url"], entry["url"]
    assert "/gui/search/" not in entry["url"], (
        "the search route lands on a comments search for a hash with no report, "
        "which looks like a broken page rather than an empty result")


def test_the_search_route_appears_nowhere_in_the_repository(app):
    """A future edit `fixing` the URL by hand is the way this regresses."""
    for entry in external.load(cd(app)).values():
        assert "/gui/search/" not in (entry.get("url") or "")
        assert "/gui/search/" not in (entry.get("availability_check") or "")


def test_the_url_carries_the_exact_canonical_hash(app):
    entry = external.load(cd(app))[KEY]
    assert entry["url"].endswith(CANONICAL_HASH), (
        "a truncated or altered hash sends the room to a different lookup, and "
        "the empty result would then be an artefact of the typo")


def test_the_lookup_is_a_third_party_tool_and_is_never_embeddable(app):
    entry = external.load(cd(app))[KEY]
    assert entry["resource_type"] == "third_party_tool"
    launch = external.resolve(cd(app), "${%s}" % KEY)
    assert launch["embeddable"] is False, (
        "a third-party tool is opened in a tab the team controls, never framed "
        "inside Predwen")


def test_the_lookup_is_not_optional(app, event):
    """It is a mission pivot now, reached from the mission's own source list."""
    entry = external.load(cd(app))[KEY]
    assert not entry.get("optional")


def test_anyrun_stays_optional_and_is_referenced_by_no_mission(app, event):
    entry = external.load(cd(app))["tool_anyrun"]
    assert entry.get("optional") is True

    referenced = set()
    for m in content.load_missions(cd(app)).values():
        for a in m.get("artifacts") or []:
            key = external.placeholder_key(a.get("external_url") or "")
            if key:
                referenced.add(key)
    assert "tool_anyrun" not in referenced


def test_the_snapshot_is_dated_and_says_it_may_differ_from_the_live_result(app):
    path = Path(app.config["ARTIFACT_DIR"]) / _artifact(app)["path"]
    snap = json.loads(path.read_text(encoding="utf-8"))

    assert snap["captured"].endswith("Z"), "the capture time is UTC and explicit"
    assert snap["query"]["value"] == CANONICAL_HASH
    assert snap["observed_result"]["state"] == "not_found"
    assert snap["what_this_supports"] and snap["what_this_does_not_support"]
    assert "may differ" in snap["warning"], (
        "the snapshot has to say out loud that it is a dated capture")


def test_the_snapshot_never_claims_to_be_live_or_to_prove_behaviour(app):
    path = Path(app.config["ARTIFACT_DIR"]) / _artifact(app)["path"]
    text = path.read_text(encoding="utf-8").lower()
    assert "not a live report" in text
    for claim in ("malicious file", "confirms the file is",
                  "proves the file", "verdict: malicious"):
        assert claim not in text, claim
    denied = " ".join(json.loads(path.read_text(encoding="utf-8"))
                      ["what_this_does_not_support"]).lower()
    assert "safe" in denied and "malicious" in denied, (
        "the snapshot must refuse both verdicts, not only the reassuring one")


def test_the_reasoning_item_is_worth_two_points_with_two_criteria(app):
    item = next(r for r in _m3(app)["rubric"] if r["key"] == RUBRIC)
    assert item["points"] == 2
    assert item["category"] == "reasoning"
    assert len(item["criteria"]) == 2


def test_the_scoping_item_keeps_its_twelve_points(app):
    """The points came out of m3_r_observed, never out of the scoping lesson."""
    item = next(r for r in _m3(app)["rubric"] if r["key"] == "m3_r_scope")
    assert item["points"] == 12


def test_mission_three_is_still_a_hundred_points_in_an_hour(app):
    m = _m3(app)
    assert m["duration_seconds"] == 3600
    assert sum(step["seconds"] for step in m["loop"]) == 3600

    budget = m["budget"]
    assert sum(budget.values()) == 100 == m["max_points"]

    by_category = {}
    for q in m["questions"]:
        by_category.setdefault(q["category"], 0)
        by_category[q["category"]] += q["points"]
        by_category.setdefault("evidence", 0)
        by_category["evidence"] += q.get("evidence_points", 0)
    for r in m["rubric"]:
        by_category.setdefault(r["category"], 0)
        by_category[r["category"]] += r["points"]

    assert by_category["reasoning"] == budget["reasoning"] == 20
    assert by_category["correctness"] == budget["correctness"] == 35


def test_no_validator_anywhere_scores_a_detection_count(app):
    import re

    for m in content.load_missions(cd(app)).values():
        for q in m.get("questions") or []:
            for accepted in (q.get("validator") or {}).get("accept") or []:
                assert not re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", str(accepted))
                assert "detection" not in str(accepted).lower()


def test_the_reasoning_item_is_answerable_without_the_lookup(app):
    """Everything the criteria ask for is in the saved copy."""
    item = next(r for r in _m3(app)["rubric"] if r["key"] == RUBRIC)
    criteria = " ".join(content.tx(c, "en") for c in item["criteria"]).lower()
    assert "safe" in criteria and "malicious" in criteria
    assert "sbx-k42" in criteria or "endpoint" in criteria

    path = Path(app.config["ARTIFACT_DIR"]) / _artifact(app)["path"]
    snap = json.loads(path.read_text(encoding="utf-8"))
    denied = " ".join(snap["what_this_does_not_support"]).lower()
    assert "sbx-k42" in denied, (
        "the saved copy has to point at the evidence the conclusion rests on")


def test_the_four_safety_constraints_are_on_the_page_in_both_languages(
        app, event, facilitator):
    _open_all(facilitator)
    for lang, lines in (
        ("en", ["Search by hash only", "Do not upload a file",
                "submit a URL", "Reanalyze"]),
        ("ja", ["ハッシュ検索だけ", "送信せず", "Reanalyze"]),
    ):
        p = join(app, event, f"Team {1 if lang == 'en' else 2}", "kenji")
        p.client.post("/lang", data={"lang": lang, "csrf_token": p.csrf})
        body = p.get(f"/artifacts/{ARTIFACT}").data.decode()
        for line in lines:
            assert line in body, f"{lang}: {line}"


def test_the_safety_lines_survive_offline_mode(app, event, facilitator):
    """Offline is exactly when a team improvises, so the warning has to stay."""
    _open_all(facilitator)
    _offline(facilitator, True)
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/artifacts/{ARTIFACT}").data.decode()
    assert "Do not upload a file" in body
    assert "Reanalyze" in body


def test_ready_offers_the_link_and_withholds_the_saved_copy(app, event,
                                                            facilitator):
    _open_all(facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/artifacts/{ARTIFACT}").data.decode()
    assert CANONICAL_HASH in body, "the launch link is the prefilled lookup"
    assert "what_this_does_not_support" not in body, (
        "while the live resource is up, the saved copy is not readable here")


def test_the_lookup_is_done_on_the_live_service_and_never_here(app, event,
                                                               facilitator):
    _open_all(facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/artifacts/{ARTIFACT}").data.decode()

    assert "what_this_does_not_support" not in body
    assert "Item not found" not in body
    assert "virustotal.com/gui/file/" in body


def test_pending_and_retired_both_show_the_saved_copy_and_no_link(
        app, event, facilitator, monkeypatch):
    _open_all(facilitator)
    for state in ("pending", "retired"):
        real = external.load

        def fake(directory, _state=state, _real=real):
            entries = dict(_real(directory))
            entry = dict(entries[KEY])
            entry["status"] = _state
            entries[KEY] = entry
            return entries

        monkeypatch.setattr(external, "load", fake)
        p = join(app, event, "Team 1", "kenji")
        body = p.get(f"/artifacts/{ARTIFACT}").data.decode()
        assert "what_this_does_not_support" in body, state
        assert f'href="https://www.virustotal.com' not in body, state
        monkeypatch.undo()


def test_the_mission_is_completable_with_the_lookup_unreachable(app, event,
                                                                facilitator):
    """The whole point of the fallback: nothing about the marks moves."""
    _open_all(facilitator)
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    _offline(facilitator, True)
    p = join(app, event, "Team 1", "kenji")
    assert p.get(f"/mission/{SLUG}").status_code == 200
    for other in ("sandbox", "file-timeline", "process-tree"):
        assert p.get(f"/artifacts/{other}").status_code == 200


RESPONSE = "m3_vt_interpretation"


def _write(p, text):
    return p.post("/api/submission", {"mission_slug": SLUG,
                                      "question_key": RESPONSE, "answer": text})


def _answers(p):
    """What the five-second poll hands back to this team's browsers."""
    return p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()["answers"]


def test_the_question_is_a_box_in_mission_three(app, event, facilitator):
    _open_all(facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = p.get(f"/mission/{SLUG}").data.decode()
    assert RESPONSE in body, "the question has to be rendered, not read aloud"
    assert "whether this file is safe or malicious" in body


def test_it_is_not_auto_scored_and_carries_no_automatic_points(app):
    from app.scoring import check_answer

    q = content.find_question(_m3(app), RESPONSE)
    assert q["points"] == 0, "the two points live on the rubric item"
    assert q["type"] not in content.AUTO_TYPES
    correct, ratio = check_answer(q, "anything at all")
    assert correct is None and ratio == 0.0


def test_one_member_writes_it_and_the_next_one_reads_it(app, event, facilitator):
    _open_all(facilitator)
    kenji = join(app, event, "Team 1", "kenji")
    assert _write(kenji, "No. It only says these engines have no verdict on "
                         "record. We are relying on SBX-K42.").status_code == 200

    yuki = join(app, event, "Team 1", "yuki")
    assert _answers(yuki)[RESPONSE]["answer"].startswith("No. It only says")


def test_another_team_cannot_see_it(app, event, facilitator):
    _open_all(facilitator)
    _write(join(app, event, "Team 1", "kenji"), "Team one's reasoning.")
    assert RESPONSE not in _answers(join(app, event, "Team 2", "aoi"))


def test_it_locks_with_the_mission(app, event, facilitator):
    _open_all(facilitator)
    p = join(app, event, "Team 1", "kenji")
    _write(p, "Written while the mission was open.")

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    assert _write(p, "Written after the close.").status_code == 409
    assert _answers(p)[RESPONSE]["answer"] == "Written while the mission was open."


def test_the_facilitator_reads_it_beside_the_criteria(app, event, facilitator):
    _open_all(facilitator)
    _write(join(app, event, "Team 1", "kenji"),
           "Not enough either way. The evidence is SBX-K42.")

    from app.models import Team
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    body = facilitator.get(f"/facilitator/grade/{team.id}").data.decode()
    assert f'data-response="{RESPONSE}"' in body, (
        "the answer has to render under the rubric item, not only in the "
        "questions table the facilitator would have to scroll back to")
    assert "Not enough either way. The evidence is SBX-K42." in body
    assert body.index("Not enough either way") < body.index(
        "Does not call the file safe"), "the answer comes before the criteria"


def test_the_export_carries_it(app, event, facilitator, tmp_path, monkeypatch):
    """Runs the real export module, against this session's database."""
    import csv
    import importlib.util
    import sys

    _open_all(facilitator)
    _write(join(app, event, "Team 1", "kenji"), "Exported reasoning.")

    script = Path(os.getcwd()) / "scripts" / "export_results.py"
    spec = importlib.util.spec_from_file_location("export_results_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "create_app", lambda: app)
    monkeypatch.setattr(sys, "argv", ["export_results.py", "--out", str(tmp_path)])
    module.main()

    written = next(Path(tmp_path).glob("*_submissions.csv"))
    rows = list(csv.DictReader(written.open(encoding="utf-8-sig")))
    mine = [r for r in rows if r["question"] == RESPONSE]
    assert mine, f"{RESPONSE} is missing from the export"
    assert "Exported reasoning." in mine[0]["answer"]


def test_the_rubric_item_is_the_only_thing_worth_points(app, event, facilitator):
    """Two manual points, and no automatic point for the paragraph itself."""
    from app.models import ScoreEvent, Team

    _open_all(facilitator)
    _write(join(app, event, "Team 1", "kenji"), "A defensible paragraph.")
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    auto = ScoreEvent.query.filter_by(team_id=team.id, question_key=RESPONSE).all()
    assert not auto, "closing must not award anything for a free_text answer"

    facilitator.post("/facilitator/api/grade", {"team_id": team.id, "mission_slug": SLUG,
                                                "rubric_key": RUBRIC, "points": 2})
    awarded = sum(e.points for e in ScoreEvent.query.filter_by(
        team_id=team.id, question_key=RUBRIC).all())
    assert awarded == 2

    over = facilitator.post("/facilitator/api/grade", {
        "team_id": team.id, "mission_slug": SLUG, "rubric_key": RUBRIC, "points": 5})
    capped = sum(e.points for e in ScoreEvent.query.filter_by(
        team_id=team.id, question_key=RUBRIC).all())
    assert capped <= 2, (
        f"a rubric item worth 2 awarded {capped} (response {over.status_code})")


def test_it_is_answerable_online_and_offline(app, event, facilitator):
    """The lookup being unreachable must not cost a team these two points."""
    _open_all(facilitator)
    for mode in (False, True):
        _offline(facilitator, mode)
        p = join(app, event, "Team 1", "kenji")
        body = p.get(f"/mission/{SLUG}").data.decode()
        assert RESPONSE in body, f"offline={mode}"
        assert _write(p, f"Answered with offline={mode}.").status_code == 200


def test_the_hash_never_appears_on_any_mission_two_surface(app, event,
                                                           facilitator):
    _open_all(facilitator)
    p = join(app, event, "Team 1", "kenji")
    for mode in (False, True):
        _offline(facilitator, mode)
        for url in (f"/mission/{M2}", "/artifacts/payload",
                    f"/mission/{SLUG}", "/team", "/evidence"):
            body = p.get(url).data.decode()
            assert CANONICAL_HASH not in body, f"{url} (offline={mode})"
