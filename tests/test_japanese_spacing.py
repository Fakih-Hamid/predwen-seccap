import pathlib
import re

import pytest
import yaml

CONTENT = pathlib.Path(__file__).resolve().parents[1] / "content"

JA = "぀-ゟ゠-ヿ一-鿿々〆ー"
PUNCT = "。、！？：；「」『』（）・…"
CLOSING = "。、！？：；」』）・…"
OPENING = "「『（"

IS_JA = re.compile(f"[{JA}{PUNCT}]")
IS_CLOSING = re.compile(f"[{CLOSING}]")
IS_OPENING = re.compile(f"[{OPENING}]")
SPACE_RUN = re.compile(" +")


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


def japanese_strings():
    for path in sorted(CONTENT.rglob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in walk(tree):
            yield path.name, key, value


def stray_spaces(text):
    hits = []
    for match in SPACE_RUN.finditer(text):
        start, end = match.span()
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if not before or not after:
            continue                       # leading/trailing, not internal
        japanese_both_sides = bool(IS_JA.match(before) and IS_JA.match(after))
        after_closing = bool(IS_CLOSING.match(before))
        before_opening = bool(IS_OPENING.match(after))
        if japanese_both_sides or after_closing or before_opening:
            hits.append((start, f"{before}{match.group(0)}{after}"))
    return hits


def all_findings():
    out = []
    for name, key, text in japanese_strings():
        for offset, fragment in stray_spaces(text):
            out.append((name, key, offset, fragment))
    return out


def test_no_japanese_string_carries_a_stray_space():
    findings = all_findings()
    report = "\n".join(f"  {n}:{k} @{o} {f!r}" for n, k, o, f in findings[:40])
    assert not findings, (
        f"{len(findings)} stray space(s) inside Japanese text:\n{report}")


@pytest.mark.parametrize("key,fragment", [
    ("collective.lead", "5種類 （"),
    ("collective.lead", "揃うと、 全"),
    ("briefing.story", "アカウントへの 不審"),
    ("landing.lead", "ミッションを 通して"),
])
def test_the_specific_fragments_that_were_fixed_do_not_return(key, fragment):
    joined = {k: v for _, k, v in japanese_strings()}
    text = joined.get(key)
    if text is None:
        pytest.skip(f"{key} no longer exists")
    assert fragment not in text, f"{key} carries {fragment!r} again"


def test_spaces_around_latin_are_left_alone():
    """The guard must not push Japanese and Latin together."""
    joined = {k: v for _, k, v in japanese_strings()}
    keep = [
            ("scoreboard.maxima", "満点：基礎点 {base}点"),
    ]
    for key, fragment in keep:
        if key in joined:
            assert fragment in joined[key], (key, fragment)

    spaced = [v for v in joined.values()
              if re.search(rf"[{JA}] [A-Za-z0-9]", v)]
    assert spaced, "every Japanese/Latin boundary lost its space"


def test_the_detector_actually_detects():
    """A guard nobody has seen fire is a guard nobody should trust."""
    assert stray_spaces("観測結果を、 通常の端末でも")          # after 、
    assert stray_spaces("クラス全体で5種類 （ハッシュ）")        # before （
    assert stray_spaces("侵害された 開発者アカウント")            # between kanji
    assert not stray_spaces("SHA-256 は ff749df7 です。")
    assert not stray_spaces("VirusTotal のレピュテーション照会")
    assert not stray_spaces("Run キー SakuraVPNCheck を作成")
    assert not stray_spaces("PID 6224 のプロセス")
    assert not stray_spaces("満点：基礎点 {base}点")
