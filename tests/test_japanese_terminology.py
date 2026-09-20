import pathlib

import pytest
import yaml

CONTENT = pathlib.Path(__file__).resolve().parents[1] / "content"


def walk(node, path=""):
    if isinstance(node, dict):
        if isinstance(node.get("ja"), str):
            yield path, node["ja"]
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            label = item["id"] if isinstance(item, dict) and "id" in item else index
            yield from walk(item, f"{path}[{label}]")


def japanese():
    out = []
    for path in sorted(CONTENT.rglob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.extend((path.name, key, text) for key, text in walk(tree))
    return out


def ui(app, key, lang="ja"):
    from app import ui as strings
    return strings.t(app.config["CONTENT_DIR"], key, lang)


def test_the_rule_no_longer_mentions_hints(app):
    for lang in ("ja", "en"):
        text = ui(app, "scoreboard.tiebreak", lang)
        assert "ヒント" not in text, text
        assert "hint" not in text.lower(), text


def test_the_rule_says_what_the_code_does(app):
    assert ui(app, "scoreboard.tiebreak", "en") == \
        "Ties break on evidence points, then team name."
    ja = ui(app, "scoreboard.tiebreak", "ja")
    assert "根拠の得点" in ja
    assert "根拠点" not in ja
    assert ja.index("根拠の得点") < ja.index("チーム名")


def test_the_code_really_sorts_that_way():
    """Read from the source, so the rule and the sort cannot drift apart."""
    import inspect

    from app import scoring

    source = inspect.getsource(scoring.leaderboard)
    assert 'key=lambda r: (-r["total"], -r["evidence"], r["name"])' in source


def test_the_sort_behaves_that_way(app, event, facilitator):
    """And observed, not just read."""
    from app.models import ScoreEvent, Team, db

    teams = Team.query.filter_by(session_id=event.id).order_by(Team.id).all()
    for team, correctness, evidence in ((teams[0], 40, 5), (teams[1], 30, 15)):
        db.session.add_all([
            ScoreEvent(session_id=event.id, team_id=team.id, source="auto",
                       category="correctness", points=correctness, actor="server"),
            ScoreEvent(session_id=event.id, team_id=team.id, source="auto",
                       category="evidence", points=evidence, actor="server")])
    db.session.commit()

    from app.scoring import leaderboard
    rows = [r for r in leaderboard(event) if r["total"] == 45]
    assert [r["name"] for r in rows] == [teams[1].display_name,
                                         teams[0].display_name], rows


def test_the_rubric_asks_what_the_english_asks(app):
    from app import missions as content

    definition = content.get_mission(app.config["CONTENT_DIR"], "digital-footprint")
    item = next(r for r in definition["rubric"] if r["key"] == "m1_r_separation")
    ja = item["prompt"]["ja"]

    assert ja == ("認証情報やアカウントについて確認できることと、"
                  "実際の使用者について確認できないことを、どのように分けましたか。")
    assert "確認できること" in ja
    assert "確認できない" in ja
    assert "人物が特定できること" not in ja


def test_the_two_m1_wordings_are_fixed(app):
    joined = {f"{name}:{key}": text for name, key, text in japanese()}
    blob = "\n".join(joined.values())
    assert "押し込まれた" not in blob, "the commit is pushed, not shoved in"
    assert "事後に失効させられています" not in blob
    assert "プッシュ" in blob
    assert "その後失効されています" not in blob, (
        "the hint states the answer again")


@pytest.mark.parametrize("banned,why", [
    ("手動評価", "the interface says 手動採点"),
    ("スケジュール済みタスク", "one spelling: スケジュールされたタスク"),
    ("スケジュールタスク", "one spelling: スケジュールされたタスク"),
    ("Runキー", "Run キー takes a space before the katakana"),
    ("対応づけ", "対応付け"),
    ("エビデンスリード", "the role is エビデンス担当"),
    ("中くらい", "中程度"),
    ("未開放", "開始前"),
    ("気にかけるチーム", "要確認チーム"),
    ("もっともらしい推論", "妥当な推論"),
    ("同じインフラにある", "同じインフラ上にある"),
    ("関係者の同定", "運用主体の特定"),
    ("端末トリアージ用証拠パック", "端末トリアージの証拠パック"),
    ("保存されたコピー", "保存済みコピー"),
    ("ページのアドレス", "ページの URL"),
    ("カバー状況", "網羅状況"),
    ("カバー範囲", "網羅範囲"),
])
def test_the_old_form_is_gone(banned, why):
    hits = [(n, k) for n, k, t in japanese() if banned in t]
    assert not hits, f"{banned!r} still present ({why}): {hits[:5]}"


def test_evidence_lead_is_not_left_inside_japanese():
    hits = [(n, k) for n, k, t in japanese() if "Evidence Lead" in t]
    assert not hits, hits


@pytest.mark.parametrize("expected", [
    "スケジュールされたタスク", "Run キー", "対応付け", "エビデンス担当",
    "手動採点", "妥当な推論", "運用主体の特定", "確認状況",
])
def test_the_new_form_is_actually_used(expected):
    assert any(expected in t for _, _, t in japanese()), expected


def test_the_four_evidence_words_all_survive():
    """No blind global replace was performed; each word keeps its own job."""
    blob = "\n".join(t for _, _, t in japanese())
    for word in ("根拠", "証拠", "エビデンス", "参考資料"):
        assert word in blob, word


def test_the_credential_word_is_harmonised_not_corrected(app):
    blob = "\n".join(t for _, _, t in japanese())
    assert "認証情報" in blob
    assert "資格情報" not in blob, (
        "two words are describing the same thing again")


def test_the_collective_board_says_what_it_is_for(app):
    title = ui(app, "collective.title")
    assert "全体ボード" in title
    assert "チーム間の情報共有" in title


def test_the_synthesis_is_named_as_the_fourth_mission(app):
    from app import missions as content

    assert ui(app, "nav.final") == "最終報告書"
    final = content.final_definition(app.config["CONTENT_DIR"])
    assert final["title"]["ja"].startswith("ミッション4")
    assert final["title"]["en"].startswith("Mission 4")
    assert final["short_label"]["ja"] == "M4"


def test_ioc_is_glossed_where_it_is_first_taught(app):
    assert "IOC（侵害指標）" in ui(app, "collective.lead")
