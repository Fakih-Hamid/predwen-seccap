import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

FILES = ["content/ui.yaml"] + [
    f"content/missions/{name}" for name in
    ("m1-digital-footprint.yaml", "m2-fake-infrastructure.yaml",
     "m3-threat-intelligence.yaml", "final-incident.yaml")]

LATIN_SENTENCE = re.compile(
    r"(?:\b[A-Za-z][A-Za-z'’\-]*\b[ ,;:]+){5,}\b[A-Za-z]")

LATIN_BY_DESIGN = {"os", "cmd", "name", "tagline"}


def bilingual(node, path=""):
    """Every {ja, en} pair in the tree, with the path that reaches it."""
    if isinstance(node, dict):
        if isinstance(node.get("ja"), str) and isinstance(node.get("en"), str):
            yield path, node["ja"], node["en"]
        for key, value in node.items():
            if key not in LATIN_BY_DESIGN:
                yield from bilingual(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from bilingual(value, f"{path}[{i}]")


def pairs(rel):
    tree = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    return list(bilingual(tree))


def is_prose(text):
    words = [w for w in re.split(r"[\s/]+", text)
             if re.fullmatch(r"[A-Za-z][A-Za-z'’\-]*", w)]
    return len(words) >= 4


@pytest.mark.parametrize("rel", FILES, ids=[f.split("/")[-1] for f in FILES])
def test_no_ja_is_just_the_english(rel):
    same = [(p, ja) for p, ja, en in pairs(rel)
            if ja.strip() and ja.strip() == en.strip() and is_prose(en)]
    assert not same, (
        f"{rel}: these `ja` values are the English string verbatim — the "
        f"translation was never written: {same}")


@pytest.mark.parametrize("rel", FILES, ids=[f.split("/")[-1] for f in FILES])
def test_no_english_sentence_survives_in_a_ja(rel):
    leaks = []
    for path, ja, _ in pairs(rel):
        found = LATIN_SENTENCE.search(ja)
        if found:
            leaks.append((path, found.group(0)[:60]))
    assert not leaks, (
        f"{rel}: untranslated English inside a `ja` string: {leaks}")


@pytest.mark.parametrize("rel", FILES, ids=[f.split("/")[-1] for f in FILES])
def test_no_ja_is_empty(rel):
    blank = [p for p, ja, _ in pairs(rel) if not ja.strip()]
    assert not blank, f"{rel}: empty `ja` values: {blank}"


def test_the_guard_would_catch_a_regression():
    """A guard nobody has seen fail is a guard nobody should trust."""
    assert LATIN_SENTENCE.search(
        "端末を開いて、Open a terminal and run the three commands below now.")
    assert LATIN_SENTENCE.search(
        "Download the original photograph and run exiftool over it.")
    for ok in ("下のコマンドを実行します: dig +short <hostname> A",
               "T1059.001 PowerShell",
               "Windows では nslookup を使ってください",
               "{answered}／{questions} 問に回答",
               "CyberChef の Decode text を UTF-16LE (1200) に設定します"):
        assert not LATIN_SENTENCE.search(ok), ok

    assert is_prose("Trace the change that rewrote the update configuration")
    for identifier in ("M2", "SR-DEV-077", "T1059.001 PowerShell",
                       "https://…", "2026-09-02T00:00:00Z", "Predwen SECCAP"):
        assert not is_prose(identifier), identifier


def test_every_how_to_step_is_bilingual():
    """The newest content, and the batch most at risk of a half-written pair."""
    seen = 0
    for rel in FILES:
        tree = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        for artifact in tree.get("artifacts") or []:
            block = artifact.get("how_to")
            if not block:
                continue
            for step in block.get("steps") or []:
                words = step.get("say") if "say" in step else step
                assert words.get("ja") and words.get("en"), artifact["id"]
                seen += 1
            if block.get("no_install"):
                assert block["no_install"].get("ja"), artifact["id"]
                assert block["no_install"].get("en"), artifact["id"]
    assert seen >= 25, f"only {seen} how_to steps found — did they move?"
