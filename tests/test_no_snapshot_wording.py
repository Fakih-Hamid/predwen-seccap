import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"

BANNED = ("saved snapshot", "スナップショット", "保存コピー")

PATH_KEYS = {"path", "fallback_snapshot", "external_url", "availability_check",
             "url", "cmd"}


def strings(node, path="", key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, f"{path}.{k}" if path else k, k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from strings(v, f"{path}[{i}]", key)
    elif isinstance(node, str) and key not in PATH_KEYS:
        yield path, node


def files():
    return sorted(CONTENT.glob("missions/*.yaml")) + [CONTENT / "ui.yaml"]


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_no_participant_string_calls_a_source_a_saved_snapshot(path):
    tree = yaml.safe_load(path.read_text(encoding="utf-8"))
    offences = [(where, text[:80]) for where, text in strings(tree)
                if any(word in text.lower() if word.isascii() else word in text
                       for word in BANNED)]
    assert not offences, (
        f"{path.name}: these tell a team the evidence is a saved copy on this "
        f"site: {offences}")


def test_the_four_that_were_fixed_by_name():
    """Named, so a revert is caught by name rather than by pattern."""
    tree = yaml.safe_load(
        (CONTENT / "missions" / "m1-digital-footprint.yaml")
        .read_text(encoding="utf-8"))
    collision = next(a for a in tree["artifacts"]
                     if a["id"] == "username-collision")

    assert collision["title"]["en"] == "Profile record collected during triage"
    assert "same-handle" not in collision["title"]["en"].lower()
    assert "same-handle" not in collision["note"]["en"].lower()
    assert collision["tool"] == "profile capture"

    question = next(q for q in tree["questions"] if q["key"] == "m1_collision")
    first = question["hints"][0]["text"]["en"].lower()
    assert "profile record" in first
    assert "same-handle" not in first

    pivot = next(h for h in tree["hints"] if h["level"] == "pivot")
    assert "profile record" in pivot["text"]["en"].lower()
    assert "same-handle" not in pivot["text"]["en"].lower()


def test_the_filenames_are_deliberately_left_alone():
    """Recorded so nobody 'finishes the job' by renaming them."""
    tree = yaml.safe_load(
        (CONTENT / "missions" / "m2-fake-infrastructure.yaml")
        .read_text(encoding="utf-8"))
    paths = {a["id"]: a.get("path") for a in tree["artifacts"]}
    assert paths["dns"] == "infrastructure/dns_snapshot.json"
    assert paths["rdap"] == "infrastructure/rdap_snapshot.json"
    for rel in paths.values():
        if rel:
            assert (ROOT / "artifacts" / rel).exists(), rel


def test_the_guard_would_catch_a_regression():
    bad = {"note": {"en": "Open the saved snapshot of the other account."}}
    assert [t for _, t in strings(bad) if "saved snapshot" in t.lower()]
    ok = {"path": "infrastructure/dns_snapshot.json"}
    assert not list(strings(ok))
    assert re.search(r"snapshot", "dns_snapshot.json")
