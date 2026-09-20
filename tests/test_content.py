import json
import os

from app import missions as content
from app import ui
from app.artifacts import resolve, sha256_of


def test_no_content_errors(app):
    errors = content.validate_all(app.config["CONTENT_DIR"], app.config["ARTIFACT_DIR"])
    assert errors == []


def test_ui_language_parity(app):
    assert ui.validate(app.config["CONTENT_DIR"]) == []


def test_default_language_is_japanese():
    from app.config import Config
    assert Config.DEFAULT_LANG == "ja"


def test_every_mission_has_both_languages(app):
    cd = app.config["CONTENT_DIR"]
    for m in content.load_missions(cd).values():
        assert content.validate_language_coverage(m, m["slug"]) == []
        ja = content.render_mission(m, "ja")
        en = content.render_mission(m, "en")
        assert ja["title"] and en["title"]
        assert ja["title"] != en["title"] or m["slug"] == "x"   # not the same string by accident


def test_budgets_add_up(app):
    cd = app.config["CONTENT_DIR"]
    for m in content.load_missions(cd).values():
        assert content.validate_budgets(m) == []
        total = sum(float(v) for v in (m.get("budget") or {}).values())
        assert abs(total - float(m["max_points"])) < 0.001


def test_three_missions_and_one_final(app):
    cd = app.config["CONTENT_DIR"]
    ms = content.ordered_missions(cd)
    assert [m["slug"] for m in ms] == ["digital-footprint", "fake-infrastructure",
                                       "threat-intelligence"]
    assert sum(m["max_points"] for m in ms) == 300
    final = content.final_definition(cd)
    assert final is not None and final["max_points"] == 150


def test_every_mission_names_an_opening_move(app):
    """Eight questions and nine sources on one screen is not a starting point."""
    cd = app.config["CONTENT_DIR"]
    for m in content.ordered_missions(cd):
        first = m.get("first_step")
        assert first, m["slug"]
        for half in ("ja", "en"):
            assert (first.get(half) or "").strip(), f"{m['slug']}: first_step.{half}"


def test_the_working_rhythm_is_machine_readable_and_adds_up(app):
    """`loop` is prose the team reads once; `seconds` is what the clock uses."""
    cd = app.config["CONTENT_DIR"]
    for m in content.ordered_missions(cd):
        steps = m.get("loop") or []
        assert steps, m["slug"]
        assert all(isinstance(s, dict) and s.get("seconds") for s in steps), m["slug"]
        total = sum(int(s["seconds"]) for s in steps)
        assert total == int(m["duration_seconds"]), f"{m['slug']}: {total}s"


def test_the_opening_move_and_the_rhythm_reach_the_participant(app):
    cd = app.config["CONTENT_DIR"]
    m = content.get_mission(cd, "digital-footprint")
    payload = content.render_mission(m, "en", cd)
    assert payload["first_step"]
    assert len(payload["phases"]) == len(m["loop"])
    assert all(p["label"] and p["seconds"] for p in payload["phases"])


def test_the_mission_prose_does_not_hand_over_an_answer(app):
    cd = app.config["CONTENT_DIR"]
    for m in content.load_missions(cd).values():
        for lang in ("ja", "en"):
            parts = [
                content.tx(m.get("narrative"), lang),
                content.tx(m.get("first_step"), lang),
                content.tx(m.get("subtitle"), lang),
                " ".join(content.tx_list(m.get("objectives"), lang)),
                " ".join(content.tx_list(m.get("loop"), lang)),
            ]
            for a in m.get("artifacts") or []:
                parts.append(content.tx(a.get("title"), lang))
                parts.append(content.tx(a.get("note"), lang))
            prose = " ".join(filter(None, parts))
            for q in m.get("questions") or []:
                accept = (q.get("validator") or {}).get("accept")
                if isinstance(accept, str):
                    accept = [accept]
                for value in accept or []:
                    if isinstance(value, str) and len(value) > 6:
                        assert value not in prose, \
                            f"{m['slug']}: the {lang} prose contains {value!r}, " \
                            f"which is the answer to {q['key']}"


def test_hint_ladder_costs(app):
    for m in content.load_missions(app.config["CONTENT_DIR"]).values():
        for h in m.get("hints") or []:
            assert h["cost"] == content.HINT_COSTS[h["level"]]


def test_artifact_files_exist_and_stay_inside_the_directory(app):
    ad = app.config["ARTIFACT_DIR"]
    for m in content.load_missions(app.config["CONTENT_DIR"]).values():
        for a in m.get("artifacts") or []:
            if a.get("path"):
                assert resolve(ad, a["path"]) is not None, a["id"]
    assert resolve(ad, "../.env") is None
    assert resolve(ad, "../../etc/passwd") is None


def test_payload_hash_matches_every_place_it_is_written(app):
    ad = app.config["ARTIFACT_DIR"]
    real = sha256_of(ad, "infrastructure/SakuraVPNUpdate_4.2.1.bin")
    assert real, "the payload artifact is missing"

    m2 = content.get_mission(app.config["CONTENT_DIR"], "fake-infrastructure")
    q = content.find_question(m2, "m2_hash")
    assert real in q["validator"]["accept"]

    with open(os.path.join(ad, "threat-intelligence", "sandbox_report.json"),
              encoding="utf-8") as f:
        sandbox = json.load(f)
    assert sandbox["sample"]["sha256"] == real

    with open(os.path.join(ad, "infrastructure", "cdn_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["artifact"]["sha256"] == real

    with open(os.path.join(ad, "threat-intelligence", "file_timeline.csv"),
              encoding="utf-8") as f:
        assert real in f.read(), "the endpoint timeline carries the same hash"


def test_no_answer_key_in_the_participant_payload(app):
    forbidden_keys = {"validator", "accept", "correct", "criteria", "facilitator_note",
                      "facilitator_notes", "text"}
    cd = app.config["CONTENT_DIR"]

    def walk(node, path=""):
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in forbidden_keys:
                    found.append(f"{path}.{key}")
                found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                found += walk(value, f"{path}[{i}]")
        return found

    for m in content.load_missions(cd).values():
        payload = content.render_mission(m, "ja")
        leaks = walk(payload, m["slug"])
        assert leaks == [], leaks

        rendered = {q["key"]: q for q in payload["questions"]}
        for q in m.get("questions") or []:
            accept = (q.get("validator") or {}).get("accept")
            if q["type"] != "short_text" or not accept:
                continue
            own = json.dumps(rendered[q["key"]], ensure_ascii=False)
            for value in (accept if isinstance(accept, list) else [accept]):
                if isinstance(value, str) and len(value) > 6:
                    assert value not in own, f"{m['slug']}/{q['key']} leaked {value!r}"


def test_hint_bodies_are_not_in_the_payload(app):
    cd = app.config["CONTENT_DIR"]
    for m in content.load_missions(cd).values():
        blob = json.dumps(content.render_mission(m, "ja"), ensure_ascii=False)
        for h in m.get("hints") or []:
            body = content.tx(h.get("text"), "ja")
            if body:
                assert body[:40] not in blob
