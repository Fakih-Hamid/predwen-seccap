import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
BASELINE = pathlib.Path(__file__).parent / "fixtures" / "content_shape.json"


SCORING_KEYS = {
    "slug", "key", "id", "type", "points", "evidence_points", "max_points",
    "budget", "correctness", "evidence", "reasoning", "completeness", "speed",
    "validator", "accept", "accept_any", "first", "must_include", "must_not",
    "exact_count", "mode", "correct", "options", "items", "nodes", "relations",
    "questions", "rubric", "response_key", "accepted_evidence",
    "needs_reasoning", "evidence_required", "final", "missions",
}


def shape(node, key=None):
    if isinstance(node, dict):
        out = {}
        for k, v in sorted(node.items()):
            if k in ("ja", "en"):
                continue
            if k == "artifacts":
                out[k] = sorted(a.get("id") for a in v if isinstance(a, dict))
                continue
            if k in ("hints", "resources", "help", "report_field"):
                continue
            if k in SCORING_KEYS or isinstance(v, (dict, list)):
                out[k] = shape(v, k)
        return out
    if isinstance(node, list):
        return [shape(x) for x in node]
    return node


SCORED_FILES = ("m1-digital-footprint.yaml", "m2-fake-infrastructure.yaml",
                "m3-threat-intelligence.yaml", "final-incident.yaml")


def current():
    out = {}
    for path in sorted(CONTENT.rglob("*.yaml")):
        if path.name not in SCORED_FILES:
            continue
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[path.name] = shape(tree)
    return out


def test_the_baseline_fixture_exists():
    assert BASELINE.exists(), (
        "regenerate with: python -c \"import json,sys; sys.path.insert(0,'.');"
        " from tests.test_japanese_pass_changed_nothing_else import current;"
        " print(json.dumps(current(), ensure_ascii=False, indent=1))\"")


def test_nothing_but_the_text_changed():
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    got = current()

    assert sorted(got) == sorted(expected), "a scored file appeared or vanished"
    for name in sorted(expected):
        assert got[name] == expected[name], (
            f"{name}: something a mark is computed from has moved since aca7ddb."
            " Do NOT regenerate the fixture to silence this — find out what.")


def test_every_answer_key_is_where_it_was(app):
    """Spelled out separately, so a failure names the question."""
    from app import missions as content

    cd = app.config["CONTENT_DIR"]
    expected = {
        "m1_key_id": ["SR-REL-2019"],
        "m1_update_url": ["update.sakura-vpn-update.com/api/channel.json"],
        "m1_flag": ["ALLOW_UNSIGNED_RECOVERY"],
        "m2_manifest_host": ["cdn.sakura-vpn-update.com/manifest.json"],
        "m2_failover_host": ["archive.sakura-vpn-update.com"],
        "m2_hash": ["ff749df77076e1243eb3c8a3b8bb049d2e7913125532b6ab2bf0b8299ef8ea31"],
        "m2_campaign": ["KITSUNE-42"],
        "m3_execution_time": ["00:19:16"],
        "m3_parent": ["SakuraVPNUpdate_4.2.1.exe"],
        "m3_exfil": ["archive.sakura-vpn-update.com"],
        "m3_upload_time": ["00:42:19"],
    }
    for slug in ("digital-footprint", "fake-infrastructure", "threat-intelligence"):
        definition = content.get_mission(cd, slug)
        for q in definition["questions"]:
            if q["key"] in expected:
                assert (q.get("validator") or {}).get("accept") == expected[q["key"]], q["key"]


def test_every_correct_option_set_is_where_it_was(app):
    from app import missions as content

    cd = app.config["CONTENT_DIR"]
    expected = {
        "m1_test": ["assertions_removed"],
        "m1_collision": ["unrelated"],
        "m1_exif": ["artist", "datetime", "model"],
        "m1_attribution": ["compromised_token"],
        "m2_signature": ["absent_expected_key"],
        "m2_shared_ip": ["shared_proxy"],
        "m2_strongest_link": ["campaign", "hash", "headers"],
        "m3_persistence": ["run_key", "sched_task"],
        "m3_scope": ["srdev077"],
        "m3_attack": ["t1041", "t1053_005", "t1059_001", "t1071_001",
                      "t1547_001", "t1560_001"],
    }
    for slug in ("digital-footprint", "fake-infrastructure", "threat-intelligence"):
        definition = content.get_mission(cd, slug)
        for q in definition["questions"]:
            if q["key"] in expected:
                got = sorted(o["id"] for o in (q.get("options") or [])
                             if o.get("correct"))
                assert got == sorted(expected[q["key"]]), (q["key"], got)


def test_the_synthesis_orderings_are_where_they_were(app):
    from app import missions as content

    final = content.final_definition(app.config["CONTENT_DIR"])
    timeline = next(q for q in final["questions"] if q["key"] == "final_timeline")
    assert timeline["validator"]["accept"] == [
        "e_registration", "e_commit", "e_publish", "e_download",
        "e_execute", "e_persist", "e_collect", "e_upload"]

    now = next(q for q in final["questions"] if q["key"] == "final_response_now")
    v = now["validator"]
    assert v["first"] == "a_isolate"
    assert sorted(v["must_include"]) == sorted(
        ["a_isolate", "a_preserve", "a_revoke", "a_channel", "a_block"])
    assert v["must_not"] == ["a_wipe"]
    assert v["exact_count"] == 5

    later = next(q for q in final["questions"]
                 if q["key"] == "final_response_later")
    v = later["validator"]
    assert v["first"] == "f_signing"
    assert sorted(v["must_include"]) == sorted(
        ["f_signing", "f_internal", "f_mfa"])
    assert v["must_not"] == ["f_blockall"]
    assert v["exact_count"] == 5

    assert not any(q["key"] == "final_response" for q in final["questions"])


def test_the_budgets_are_where_they_were(app):
    from app import missions as content

    cd = app.config["CONTENT_DIR"]
    for slug in ("digital-footprint", "fake-infrastructure", "threat-intelligence"):
        d = content.get_mission(cd, slug)
        assert d["max_points"] == 100
        assert d["budget"] == {"correctness": 35, "evidence": 30,
                               "reasoning": 20, "completeness": 10, "speed": 5}
    assert content.final_definition(cd)["max_points"] == 150


@pytest.mark.parametrize("key,placeholders", [
    ("mission.progress_answered", {"answered", "questions"}),
    ("mission.review_counts", {"answered", "questions", "evidence"}),
    ("scoreboard.maxima", {"base", "bonus", "total"}),
    ("facilitator.lock_blocked", {"remaining", "teams"}),
    ("facilitator_lock.progress", {"done", "total"}),
    ("final.review_counts", {"filled", "total"}),
])
def test_placeholders_match_between_the_languages(app, key, placeholders):
    """A `{teams}` lost in a rewrite renders as a literal brace on a projector."""
    import re

    from app import ui

    cd = app.config["CONTENT_DIR"]
    found = {}
    for lang in ("ja", "en"):
        text = ui.t(cd, key, lang)
        assert not text.startswith("⟦"), (key, lang)
        found[lang] = set(re.findall(r"\{([a-z_]+)\}", text))
    assert found["ja"] == placeholders, (key, found["ja"])
    assert found["en"] == placeholders, (key, found["en"])
