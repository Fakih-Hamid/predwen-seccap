import pathlib
import re

import pytest
import yaml

from .conftest import join, open_all_sources, wind_forward

ROOT = pathlib.Path(__file__).resolve().parents[1]
MISSIONS = ROOT / "content" / "missions"


def load(name):
    return yaml.safe_load((ROOT / "content" / name).read_text(encoding="utf-8"))


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def test_m1_recovery_hint_spells_account_correctly():
    import json

    m1 = load("missions/m1-digital-footprint.yaml")
    blob = json.dumps(m1, ensure_ascii=False)
    assert "アカント" not in blob
    assert "アカウント" in blob
    for path in MISSIONS.glob("*.yaml"):
        assert "アカアウント" not in path.read_text(encoding="utf-8"), path.name


def test_m3_scope_pivot_is_clear_japanese():
    m3 = load("missions/m3-threat-intelligence.yaml")
    q = next(q for q in m3["questions"] if q["key"] == "m3_scope")
    rung = next(h for h in q["hints"] if h["level"] == "pivot")
    ja = rung["text"]["ja"]
    assert "実行" in ja
    assert "永続化" in ja
    assert "侵害" in ja
    assert "residence" not in ja


def test_terminal_and_endpoint_are_different_words():
    m1 = load("missions/m1-digital-footprint.yaml")
    photo = next(a for a in m1["artifacts"] if a["id"] == "photo-exif")

    def ja_of(step):
        return (step.get("say") or {}).get("ja") if "say" in step else step.get("ja")

    assert any("ターミナル" in (ja_of(s) or "") for s in photo["how_to"]["steps"])

    m2 = load("missions/m2-fake-infrastructure.yaml")
    gateway = next(a for a in m2["artifacts"] if a["id"] == "update-service")
    assert any("ターミナル" in (ja_of(s) or "") for s in gateway["how_to"]["steps"])
    assert not any("端末" in (ja_of(s) or "") for s in gateway["how_to"]["steps"]), (
        "端末 is an endpoint here; the terminal application is ターミナル")

    m3 = load("missions/m3-threat-intelligence.yaml")
    labels = [c["os"]["ja"] for a in m3["artifacts"]
              for c in (a.get("how_to") or {}).get("commands") or []
              if isinstance(c.get("os"), dict)]
    assert labels.count("macOS / Linux（ターミナルを使う場合）") == 4
    assert not any("端末を使う場合" in label for label in labels)
    scope = next(q for q in m3["questions"] if q["key"] == "m3_scope")
    assert "端末" in scope["prompt"]["ja"]


@pytest.mark.parametrize("name", ["m1-digital-footprint.yaml",
                                  "m2-fake-infrastructure.yaml",
                                  "m3-threat-intelligence.yaml"])
def test_the_closing_loop_step_names_no_role(name):
    last = load(f"missions/{name}")["loop"][-1]
    assert last["seconds"] == 480
    assert "ロック" in last["ja"]
    assert "lock" in last["en"].lower()
    assert "Evidence Lead" not in last["en"]
    assert "エビデンス担当" not in last["ja"]


def test_key_ui_wording_stays_clear_and_consistent():
    ui = load("ui.yaml")
    assert "{questions}" in ui["mission"]["done_body"]["ja"]
    assert "{evidence}" in ui["mission"]["done_body"]["ja"]
    assert "{count}" in ui["mission"]["review_more_coming"]["ja"]
    assert "回答に使用できません" in ui["mission"]["review_more_coming"]["ja"]
    assert "根拠の得点" in ui["scoreboard"]["tiebreak"]["ja"]
    assert "担当" in ui["assistant"]["pick_lead"]["ja"]
    assert "作業分担は任意" in ui["mission"]["while_you_wait_body"]["ja"]


def test_the_three_orphaned_keys_are_gone():
    ui = load("ui.yaml")
    assert "role" not in ui["join"]
    assert "role_auto" not in ui["join"]
    assert "roles_title" not in ui["briefing"]
    for path in list((ROOT / "app").rglob("*.html")) + list((ROOT / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for key in ("join.role", "role_auto", "roles_title"):
            assert key not in text, (path.name, key)


def test_terminology_split_is_preserved():
    ui = load("ui.yaml")
    assert "根拠" in ui["scoreboard"]["tiebreak"]["ja"]
    assert "根拠" in ui["mission"]["done_body"]["ja"]
    assert ui["nav"]["resources"]["ja"] == "参考資料"
    assert ui["nav"]["evidence"]["ja"] == "エビデンスボード"
    assert ui["mission"]["artifacts"]["ja"] == "資料"


def test_removed_terms_do_not_return_to_participant_copy():
    for path in [ROOT / "content" / "ui.yaml",
                 ROOT / "content" / "external_resources.yaml",
                 *MISSIONS.glob("*.yaml")]:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("ja"), str):
                    yield node["ja"]
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)

        for ja in walk(data):
            assert "道具" not in ja, (path.name, ja)
            assert "持ち出し" not in ja, (path.name, ja)


SLUG = "digital-footprint"


def test_corrected_strings_reach_screens_in_japanese(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, "ja")

    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert "作業分担は任意" in html

    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert "回答" in html
    assert "根拠" in html

    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    html = p.get("/scoreboard").get_data(as_text=True)
    assert "同点の場合は根拠の得点を比較し" in html

    wind_forward(app, event, SLUG, 1500)
    body = p.post("/api/hint", {"mission_slug": SLUG, "hint_id": "m1_h5"}).get_json()
    assert body["hint"]["text"]
    assert "認証情報" in body["hint"]["text"]

    open_all_sources(app, event, SLUG)
    html = p.get("/artifacts/photo-exif").get_data(as_text=True)
    assert "ターミナル" in html
    assert "端末でファイル" not in html


def test_m3_labels_render_on_every_csv_page(app, event, facilitator):
    facilitator.post("/facilitator/mission/threat-intelligence/open", {})
    wind_forward(app, event, "threat-intelligence")
    open_all_sources(app, event, "threat-intelligence")
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, "ja")
    seen = 0
    for artifact in ("persistence", "file-timeline", "network", "powershell-log"):
        html = p.get(f"/artifacts/{artifact}").get_data(as_text=True)
        if "macOS / Linux（ターミナルを使う場合）" in html:
            seen += 1
        assert "端末を使う場合" not in html, artifact
    assert seen == 4


def test_no_participant_screen_carries_a_review_notice(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    p = join(app, event, "Team 1", "kenji")
    for lang in ("en", "ja"):
        speak(p.client, lang)
        for path in ("/team", "/briefing", f"/mission/{SLUG}", "/artifacts/commit",
                     "/evidence", "/final", "/scoreboard"):
            html = p.get(path).get_data(as_text=True)
            body = re.sub(r"<!--.*?-->", "", html.split("</header>", 1)[-1], flags=re.S)
            low = body.lower()
            for notice in ("provisional japanese", "native speaker", "native-speaker",
                           "not yet reviewed", "暫定訳", "ネイティブ", "未校正"):
                assert notice not in low, (lang, path, notice)
