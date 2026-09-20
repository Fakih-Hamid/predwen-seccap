import csv
import io
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MISSIONS = ROOT / "content" / "missions"

TABLES = {
    "persistence": "threat-intelligence/persistence.csv",
    "file-timeline": "threat-intelligence/file_timeline.csv",
    "network": "threat-intelligence/network_connections.csv",
}

COLUMN_EN = re.compile(r"\b(?:the\s+)?([a-z_][a-z0-9_]{2,})\s+column\b", re.I)
COLUMN_JA = re.compile(r"([A-Za-z_][A-Za-z0-9_]{2,})\s*列")

NOT_A_HEADER = {"same", "first", "last", "one", "that", "this",
                "each", "other", "right", "wrong", "header"}


def headers(rel):
    text = (ROOT / "artifacts" / rel).read_text(encoding="utf-8")
    return [h.strip().lower() for h in next(csv.reader(io.StringIO(text)))]


def mission(name):
    return yaml.safe_load((MISSIONS / name).read_text(encoding="utf-8"))


def artifact(name, artifact_id):
    return next(a for a in mission(name)["artifacts"] if a["id"] == artifact_id)


def strings_of(block):
    """Every authored string in a how_to block and its ladder."""
    out = []
    for step in (block.get("how_to") or {}).get("steps") or []:
        out += [step.get("en", ""), step.get("ja", "")]
    no_install = (block.get("how_to") or {}).get("no_install") or {}
    out += [no_install.get("en", ""), no_install.get("ja", "")]
    for rung in block.get("hints") or []:
        out += [rung["text"].get("en", ""), rung["text"].get("ja", "")]
    return [s for s in out if s]


@pytest.mark.parametrize("artifact_id,rel", sorted(TABLES.items()))
def test_every_column_an_instruction_names_is_in_the_file(artifact_id, rel):
    real = headers(rel)
    source = artifact("m3-threat-intelligence.yaml", artifact_id)

    wrong = []
    for text in strings_of(source):
        named = {m.lower() for m in COLUMN_EN.findall(text)}
        named |= {m.lower() for m in COLUMN_JA.findall(text)}
        for name in named - NOT_A_HEADER:
            if not any(name in column for column in real):
                wrong.append((name, text[:70]))
    assert not wrong, (
        f"{artifact_id} tells a team to read a column that {rel} does not "
        f"have. Its header is {real}. Offending: {wrong}")


@pytest.mark.parametrize("artifact_id,rel", sorted(TABLES.items()))
def test_the_japanese_uses_the_literal_header_not_a_translation(artifact_id,
                                                                rel):
    """`event 列`, not 動作の列. A column name is what the header row says."""
    source = artifact("m3-threat-intelligence.yaml", artifact_id)
    translated = []
    for text in strings_of(source):
        for word in ("動作の列", "動作列", "結果の列", "ホスト列", "宛先の列"):
            if word in text:
                translated.append((word, text[:60]))
    assert not translated, (
        f"{artifact_id} names a column in translation: {translated}. "
        f"The header is {headers(rel)}.")


PLACEHOLDER = re.compile(r"<[^>\s]{1,24}>")


def how_to_blocks():
    for path in sorted(MISSIONS.glob("m*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8"))
        for artifact in tree.get("artifacts") or []:
            if artifact.get("how_to"):
                yield artifact["id"], artifact["how_to"]


BLOCKS = list(how_to_blocks())


@pytest.mark.parametrize("artifact_id,block", BLOCKS,
                         ids=[a for a, _ in BLOCKS])
def test_a_step_names_a_placeholder_exactly_as_the_command_writes_it(
        artifact_id, block):
    literals = set(PLACEHOLDER.findall(
        " ".join(c.get("cmd", "") for c in block.get("commands") or [])))
    texts = [(lang, step.get(lang) or "")
             for step in block.get("steps") or [] for lang in ("ja", "en")]
    texts += [(lang, (block.get("no_install") or {}).get(lang) or "")
              for lang in ("ja", "en")]

    wrong = [(lang, named) for lang, text in texts
             for named in PLACEHOLDER.findall(text) if named not in literals]
    assert not wrong, (
        f"{artifact_id}: a step names a placeholder the commands do not "
        f"write: {wrong}. The commands use {sorted(literals)}.")


def test_the_dns_command_can_be_run_as_printed():
    import json

    block = dict(BLOCKS)["dns"]
    whole = json.dumps(block, ensure_ascii=False)
    assert "<hostname>" not in whole and "<ホスト名>" not in whole
    for cmd in block.get("commands") or []:
        assert "update.sakura-vpn-update.com" in cmd["cmd"], cmd["cmd"]
    assert "X-Sakura-Manifest" in whole and "X-Sakura-Failover" in whole


def test_the_file_timeline_regression_by_name():
    """Named, so a revert is caught by name rather than by pattern."""
    source = artifact("m3-threat-intelligence.yaml", "file-timeline")
    joined = " ".join(strings_of(source))
    assert "event" in joined
    assert "action column" not in joined.lower()
    assert "event" in headers(TABLES["file-timeline"])
    assert "action" not in headers(TABLES["file-timeline"])


def test_the_network_table_really_does_have_an_action_column():
    assert "action" in headers(TABLES["network"])


@pytest.mark.parametrize("artifact_id,rel", sorted(TABLES.items()))
def test_the_guard_would_catch_a_regression(artifact_id, rel):
    real = headers(rel)
    invented = "sausage"
    assert not any(invented in column for column in real)
    assert COLUMN_EN.findall(f"Read the {invented} column carefully.")
    assert COLUMN_JA.findall(f"{invented} 列を読みます。")
