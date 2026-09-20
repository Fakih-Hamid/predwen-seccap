import hashlib
import json
import os

from app import external, missions as content
from app.models import EventSession, db

from .conftest import join, open_all_sources, wind_forward

SLUG = "digital-footprint"
M2 = "fake-infrastructure"
CANONICAL_HASH = "ff749df77076e1243eb3c8a3b8bb049d2e7913125532b6ab2bf0b8299ef8ea31"
TOKEN_AUDIT_HASH = "173daab1c9ad20ce67e03ff35b2151aba5feb1598a84c90909f5b93f040a6724"


def cd(app):
    return app.config["CONTENT_DIR"]


def _open_all(app, event, facilitator):
    for slug in (SLUG, M2, "threat-intelligence"):
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)


def test_the_servers_hash_of_the_payload_never_reaches_a_participant(app, event,
                                                                     facilitator):
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")

    pages = ["/mission/" + M2, "/artifacts/payload", "/mission/" + SLUG,
             "/team", "/evidence"]
    for url in pages:
        body = p.get(url).data.decode()
        assert CANONICAL_HASH not in body, url


def test_the_payload_page_carries_the_instruction_and_not_the_value(app, event,
                                                                   facilitator):
    """"Compute the SHA-256" has to stay. The value must not appear."""
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/payload").data.decode()

    assert "SHA-256" in body, "the pivot tells them to compute it"
    assert CANONICAL_HASH not in body
    assert "data-copy=" not in body


def test_the_manifest_snapshot_still_publishes_its_own_hash(app, event, facilitator):
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/cdn-manifest").data.decode()

    assert "sakura-cdn.pages.dev/manifest.json" in body
    assert "not_present" not in body, "the manifest was rendered inside Predwen"
    assert CANONICAL_HASH not in body


def test_a_published_payload_is_fetched_from_the_web_and_not_from_here(
        app, event, facilitator):
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")

    res = p.get("/artifacts/payload/download")
    assert res.status_code == 409
    assert res.get_json()["error"] == "use_the_external_resource"
    assert res.get_json()["url"].endswith("SakuraVPNUpdate_4.2.1.bin")


def test_the_photograph_is_fetched_from_the_web_so_exiftool_reads_the_original(
        app, event, facilitator):
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")

    res = p.get("/artifacts/photo-exif/download")
    assert res.status_code == 409
    assert res.get_json()["url"].endswith(".jpg")

    body = p.get("/artifacts/photo-exif").data.decode()
    assert "exiftool" in body.lower()


def test_the_download_refuses_while_the_live_resource_is_up(app, event, facilitator):
    """Same rule as the page. Otherwise this endpoint reopens the door."""
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    res = p.get("/artifacts/payload/download")
    assert res.status_code == 409
    assert res.get_json()["error"] == "use_the_external_resource"


def test_a_published_source_hands_over_no_bytes(app, event, facilitator):
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")
    response = p.get("/artifacts/username-collision/download")
    assert response.status_code == 409
    assert response.get_json()["error"] == "use_the_external_resource"


def test_the_download_takes_an_id_and_never_a_path(app, event, facilitator):
    """No browser-supplied path is ever joined to anything."""
    _open_all(app, event, facilitator)
    p = join(app, event, "Team 1", "kenji")

    for hostile in ("../../.env", "..%2f..%2f.env", "/etc/passwd", "nonexistent"):
        assert p.get(f"/artifacts/{hostile}/download").status_code in (404, 405), hostile


def test_an_artifact_of_a_locked_mission_does_not_download(app, event, facilitator):
    """The same reachability rule the viewer uses."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    assert p.get("/artifacts/sandbox/download").status_code == 404


def test_the_download_is_capped(app):
    from app.artifacts import DOWNLOAD_MAX_BYTES
    assert DOWNLOAD_MAX_BYTES <= 16 * 1024 * 1024


def test_the_token_evidence_is_the_canonical_copy_byte_for_byte(app):
    path = f"{app.config['ARTIFACT_DIR']}/digital-footprint/token_audit.json"
    with open(path, "rb") as f:
        raw = f.read()

    assert hashlib.sha256(raw).hexdigest() == TOKEN_AUDIT_HASH, (
        "the served copy no longer matches the canonical artifact in "
        "sakura-vpn-update-infrastructure (audit/token_audit.json). Copy it "
        "across again rather than editing this one")
    assert b"\r\n" not in raw, (
        "CRLF in a hashed artifact: check `artifacts/** -text` in .gitattributes")

    canonical = os.environ.get("SECCAP_SCENARIO_REPO")
    if canonical:
        with open(os.path.join(canonical, "audit", "token_audit.json"), "rb") as f:
            assert f.read() == raw, "the two copies have diverged"


def test_mission_one_carries_the_token_evidence(app):
    """The synthesis asserts a compromised token. Something has to show it."""
    m = content.get_mission(cd(app), SLUG)
    entry = content.find_artifact(m, "token-audit")
    assert entry is not None
    assert entry.get("external_url") == "${m1_token_audit}"
    assert entry.get("path") or entry.get("fallback_snapshot")

    with open(f"{app.config['ARTIFACT_DIR']}/digital-footprint/token_audit.json",
              encoding="utf-8") as f:
        audit = json.load(f)

    push = audit["repository_events"][0]
    assert push["commit"] == "85902d17af7621d58ddaad0f1a306ffc1bff23fb"
    assert push["authentication"] == "personal_access_token"
    assert push["review"] == "none"

    credential = audit["credential_inventory"][0]
    assert credential["associated_account"] == "sora-dev77"
    assert credential["status"] == "revoked"
    assert credential["fingerprint"]

    assert any("who held the token" in line.lower()
               for line in audit["what_this_does_not_support"])


def test_the_token_evidence_contains_no_credential_and_no_real_address(app):
    import re

    with open(f"{app.config['ARTIFACT_DIR']}/digital-footprint/token_audit.json",
              encoding="utf-8") as f:
        raw = f.read()

    for ip in ("192.0.2.24", "192.0.2.183"):
        assert ip in raw
    addresses = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", raw))
    outside = sorted(a for a in addresses if not a.startswith("192.0.2."))
    assert not outside, f"addresses outside TEST-NET-1: {outside}"

    for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"):
        assert prefix not in raw, prefix
    assert not [h for h in re.findall(r"\b[0-9a-f]{40}\b", raw.lower())
                if h != "85902d17af7621d58ddaad0f1a306ffc1bff23fb"]

    assert "synthetic" not in raw.lower()
    assert "training_notice" not in raw


def test_the_answer_is_compromised_not_stolen(app):
    m = content.get_mission(cd(app), SLUG)
    q = content.find_question(m, "m1_attribution")
    correct = [o for o in q["options"] if o.get("correct")]
    assert len(correct) == 1
    label = content.tx(correct[0]["label"], "en").lower()
    assert "compromised" in label
    assert "stolen" not in label
    assert "cannot be identified" in label

    ids = {o["id"] for o in q["options"]}
    assert "stolen_token" in ids, "the over-claim has to be offerable"
    assert "token-audit" in q["accepted_evidence"]


def test_no_participant_text_calls_the_token_stolen(app):
    cdir = cd(app)
    for m in content.load_missions(cdir).values():
        payload = content.render_mission(m, "en", cdir)
        for question in payload["questions"]:
            for field in ("prompt", "help", "placeholder"):
                assert "stolen" not in (question.get(field) or "").lower(), \
                    f"{m['slug']}/{question['key']}.{field}"
            for option in question.get("options") or []:
                if option["id"] == "stolen_token":
                    continue                      # the over-claim, on purpose
                assert "stolen" not in option["label"].lower(), \
                    f"{m['slug']}/{question['key']}/{option['id']}"
        for field in ("narrative", "subtitle", "first_step"):
            assert "stolen" not in (payload.get(field) or "").lower(), \
                f"{m['slug']}.{field}"
        for hint in payload["hints"]:
            assert "stolen" not in (hint.get("label") or "").lower(), m["slug"]


def test_every_rubric_item_criteria_add_up_to_its_points(app):
    import re

    for m in content.load_missions(cd(app)).values():
        for item in m.get("rubric") or []:
            criteria = item.get("criteria")
            if not isinstance(criteria, list):
                continue
            total = 0
            for line in criteria:
                text = content.tx(line, "en") or ""
                match = re.search(r"\((\d+)\s*pts?\)", text)
                assert match, f"{m['slug']}/{item['key']}: {text[:60]}"
                total += int(match.group(1))
            assert total == int(item["points"]), (
                f"{m['slug']}/{item['key']}: criteria total {total} != "
                f"{item['points']} points")


def _response_ratio(app, order, key="final_response_now"):
    from app.scoring import check_answer
    m = content.get_mission(cd(app), "final-incident")
    return check_answer(content.find_question(m, key), order)


def _saved_response(app, event, facilitator, response):
    """POST an ordering the way a crafted request would, read back what stuck."""
    from app.models import FinalReport, Team
    facilitator.post("/facilitator/session/state", {"final_open": True})
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/final", {"response": response})
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    report = FinalReport.query.filter_by(team_id=team.id).one()
    return json.loads(report.response_json)


def test_an_unoffered_action_is_not_stored(app, event, facilitator):
    saved = _saved_response(app, event, facilitator, [
        "a_isolate", "call the police", "a_preserve", "<script>x</script>",
        "a_revoke", "a_channel", "a_block"])
    assert saved == ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"]


def test_a_repeated_action_is_collapsed_at_the_door(app, event, facilitator):
    """Ranking the same action twice would push a real one out of the top five."""
    saved = _saved_response(app, event, facilitator, [
        "a_isolate", "a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"])
    assert saved == ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"]
    assert len(saved) == len(set(saved))


def test_exactly_five_correct_actions_score_full(app):
    correct, ratio = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"])
    assert correct is True and ratio == 1.0


def test_a_sixth_action_does_not_score_full(app):
    full = ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"]
    correct, ratio = _response_ratio(app, full + ["a_scope"])
    assert correct is False
    assert 0 < ratio < 1.0


def test_a_repeated_action_does_not_score_full(app):
    """A ranked list that names the same action twice is malformed."""
    correct, ratio = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_revoke", "a_block"])
    assert correct is False and ratio < 1.0

    _, padded = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_channel"])
    _, clean = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"])
    assert padded < clean


def test_several_defensible_response_orders_all_score_full(app):
    """Revoke, channel and block in any order is a judgement, not an error."""
    base = ["a_isolate", "a_preserve"]
    for tail in (["a_revoke", "a_channel", "a_block"],
                 ["a_revoke", "a_block", "a_channel"],
                 ["a_channel", "a_revoke", "a_block"],
                 ["a_block", "a_revoke", "a_channel"]):
        correct, ratio = _response_ratio(app, base + tail)
        assert correct is True and ratio == 1.0, tail


def test_isolating_late_loses_points(app):
    correct, ratio = _response_ratio(
        app, ["a_revoke", "a_isolate", "a_preserve", "a_channel", "a_block"])
    assert correct is False and 0 < ratio < 1.0


def test_leaving_out_a_required_action_loses_points(app):
    correct, ratio = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_scope"])
    assert correct is False and 0 < ratio < 1.0


def test_a_harmful_action_in_the_top_five_loses_points(app):
    correct, ratio = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_wipe", "a_revoke", "a_block"])
    assert correct is False
    assert ratio < 1.0


def _later_ratio(app, order):
    return _response_ratio(app, order, key="final_response_later")


def test_the_five_hardening_changes_score_full(app):
    correct, ratio = _later_ratio(
        app, ["f_signing", "f_internal", "f_mfa", "f_review", "f_egress"])
    assert correct is True and ratio == 1.0


def test_signing_has_to_come_first(app):
    """It is the one change that would have stopped THIS incident."""
    correct, ratio = _later_ratio(
        app, ["f_mfa", "f_signing", "f_internal", "f_review", "f_egress"])
    assert correct is False and 0 < ratio < 1.0


def test_blocking_every_unseen_domain_loses_points(app):
    correct, ratio = _later_ratio(
        app, ["f_signing", "f_internal", "f_mfa", "f_blockall", "f_egress"])
    assert correct is False and ratio < 1.0


def test_a_sixth_hardening_change_does_not_score_full(app):
    correct, ratio = _later_ratio(
        app, ["f_signing", "f_internal", "f_mfa", "f_review", "f_egress",
              "f_tokens"])
    assert correct is False and ratio < 1.0


def test_wiping_before_preserving_breaks_two_rules_at_once(app):
    """It is both a harmful action and an ordering failure."""
    _, with_wipe = _response_ratio(
        app, ["a_isolate", "a_wipe", "a_preserve", "a_revoke", "a_block"])
    _, clean = _response_ratio(
        app, ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"])
    assert with_wipe < clean


def test_the_retired_scenario_has_not_come_back(app):
    """Telegram and sakura-lab.test, across content and artifacts."""
    import os
    import re

    pattern = re.compile(r"telegram|sakura-lab\.test|mail\.invalid|dea12541", re.I)
    for root in (cd(app), app.config["ARTIFACT_DIR"]):
        for base, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(base, name)
                with open(path, "rb") as f:
                    raw = f.read()
                text = raw.decode("utf-8", "ignore")
                assert not pattern.search(text), path


def test_a_missing_manifest_is_not_an_error(app, tmp_path):
    external.clear_cache()
    try:
        assert external.load(str(tmp_path)) == {}
        assert external.validate(str(tmp_path)) == []
    finally:
        external.clear_cache()


def test_the_registration_time_is_the_one_in_the_rdap_snapshot(app):
    """Mission 2 asks a team to compare it with the commit."""
    with open(f"{app.config['ARTIFACT_DIR']}/infrastructure/rdap_snapshot.json",
              encoding="utf-8") as f:
        rdap = json.load(f)
    registration = next(e["eventDate"] for e in rdap["events"]
                        if e["eventAction"] == "registration")
    assert registration == "2026-08-27T18:15:10Z"

    final = content.get_mission(cd(app), "final-incident")
    labels = " ".join(content.tx(e["label"], "en")
                      for e in final["timeline_events"])
    assert "08-27" in labels, "the synthesis timeline carries the registration"
