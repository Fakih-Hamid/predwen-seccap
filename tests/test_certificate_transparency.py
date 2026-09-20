import json
import pathlib

import pytest
import yaml

from app import external

from .conftest import join

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "content" / "missions"
KEY = "tool_certkit"
URL = "https://www.certkit.io/tools/ct-logs/?query=sakura-vpn-update.com"
CAPTURE = ROOT / "artifacts" / "infrastructure" / "ct_log_search.json"


def manifest():
    raw = yaml.safe_load((ROOT / "content" / "external_resources.yaml")
                         .read_text(encoding="utf-8"))
    return {e["key"]: e for e in raw["resources"]}


def test_the_manifest_declares_certkit_at_the_verified_url():
    entry = manifest()[KEY]
    assert entry["url"] == URL
    assert entry["availability_check"] == URL
    assert entry["status"] == "ready"
    assert entry["resource_type"] == "third_party_tool"
    assert entry["optional"] is True
    assert URL.startswith("https://www.certkit.io/")


def test_crt_sh_is_gone_from_everything_a_participant_can_reach():
    assert "tool_crtsh" not in manifest()

    looked = []
    for path in list((ROOT / "content").rglob("*.yaml")) + \
            list((ROOT / "app" / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        looked.append(path)
        assert "crt.sh/?" not in text, path
        assert "https://crt.sh" not in text, path
    assert len(looked) > 5, "the sweep found nothing to look at"


def test_the_capture_replaced_the_dns_snapshot_as_the_fallback():
    entry = manifest()[KEY]
    assert entry["fallback_snapshot"] == "infrastructure/ct_log_search.json"
    assert CAPTURE.exists()


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_note_tells_them_which_two_columns_to_read(lang):
    note = manifest()[KEY]["participant_note"][lang]
    assert "Common Name" in note
    assert "SAN" in note
    assert ("no account" in note.lower() if lang == "en"
            else "アカウントは不要" in note)


def test_every_optional_tool_carries_one():
    thin = []
    for key, entry in manifest().items():
        if not entry.get("optional"):
            continue
        note = entry.get("participant_note") or {}
        for half in ("ja", "en"):
            if not (note.get(half) or "").strip():
                thin.append(f"{key}[{half}]")
    assert not thin, f"optional tools with no participant note: {thin}"


def test_the_note_is_a_pair_and_the_validator_would_catch_a_half():
    entry = manifest()[KEY]
    assert set(entry["participant_note"]) == {"ja", "en"}

    broken = {"resources": [dict(entry, participant_note={"en": "only english"})],
              "version": "2.0"}
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "external_resources.yaml"
        path.write_text(yaml.safe_dump(broken), encoding="utf-8")
        external.clear_cache()
        errors = external.validate(tmp)
    external.clear_cache()
    assert any("participant_note has no 'ja' half" in e for e in errors), errors


def test_the_capture_is_a_faithful_record_of_what_the_tool_returns():
    data = json.loads(CAPTURE.read_text(encoding="utf-8"))
    assert data["result_count"] == 7
    assert len(data["certificates"]) == 7
    assert data["source"] == URL

    names = {c["common_name"] for c in data["certificates"]}
    assert names == {"archive.sakura-vpn-update.com",
                     "update.sakura-vpn-update.com",
                     "cdn.sakura-vpn-update.com",
                     "sakura-vpn-update.com"}
    wildcard = next(c for c in data["certificates"]
                    if "*.sakura-vpn-update.com" in c["matching_sans"])
    assert wildcard["issuer"] == "Sectigo Limited"
    assert sorted(wildcard["matching_sans"]) == ["*.sakura-vpn-update.com",
                                                 "sakura-vpn-update.com"]


def test_the_capture_cannot_answer_a_question_on_its_own():
    text = CAPTURE.read_text(encoding="utf-8").lower()
    for role in ("failover", "manifest", "gateway", "payload"):
        assert role not in text, f"the capture assigns a role: {role!r}"
    assert "/manifest.json" not in text
    assert "manifest.json" not in text


def opened(app, event, facilitator):
    facilitator.post("/facilitator/mission/fake-infrastructure/open", {})
    return join(app, event, "Team 1", "kenji")


def test_no_mission_references_it_and_no_question_is_scored_on_it():
    """`Preserve all canonical answers and scoring` — asserted, not assumed."""
    for path in sorted(MANIFEST.glob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for a in tree.get("artifacts") or []:
            assert external.placeholder_key(a.get("external_url") or "") != KEY
            assert external.placeholder_key(
                (a.get("how_to") or {}).get("tool_link") or "") != KEY
        for q in tree.get("questions") or []:
            assert KEY not in (q.get("accepted_evidence") or [])
