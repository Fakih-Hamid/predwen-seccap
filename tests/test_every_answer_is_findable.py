import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
MISSIONS = ROOT / "content" / "missions"


def cases():
    """(mission, question key, accepted value, {artifact id: text})."""
    for path in sorted(MISSIONS.glob("m*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8"))
        artifacts = {a["id"]: a for a in tree.get("artifacts") or []}
        for question in tree.get("questions") or []:
            validator = question.get("validator") or {}
            if validator.get("kind") != "contains":
                continue
            named = question.get("accepted_evidence") or []
            for value in validator.get("accept", []):
                yield tree["slug"], question["key"], value, named, artifacts


CASES = [(slug, key, value, named, artifacts)
         for slug, key, value, named, artifacts in cases()]

assert CASES, "no contains-validated questions found — did the schema change?"


def text_of(artifacts, artifact_id):
    entry = artifacts.get(artifact_id) or {}
    rel = entry.get("path") or entry.get("fallback_snapshot")
    if not rel:
        return None
    path = ARTIFACTS / rel
    if not path.exists():
        return None
    return path.read_bytes().decode("utf-8", "replace").lower()


@pytest.mark.parametrize(
    "slug,key,value,named,artifacts", CASES,
    ids=[f"{k}-{v[:18]}" for _, k, v, _, _ in CASES])
def test_the_answer_is_in_a_source_the_question_names(slug, key, value, named,
                                                      artifacts):
    assert named, f"{key} accepts {value!r} but names no source to find it in"

    found = [aid for aid in named
             if (text_of(artifacts, aid) or "") and
             value.lower() in text_of(artifacts, aid)]
    assert found, (
        f"{slug}/{key} accepts {value!r}, and it appears in none of the "
        f"sources the question names ({named}). Either the answer key drifted "
        f"from the material, or the question points at the wrong source.")


@pytest.mark.parametrize(
    "slug,key,value,named,artifacts", CASES,
    ids=[f"{k}-{v[:18]}" for _, k, v, _, _ in CASES])
def test_every_named_source_has_a_file_behind_it(slug, key, value, named,
                                                 artifacts):
    for artifact_id in named:
        entry = artifacts.get(artifact_id)
        assert entry, f"{key} names an artifact that is not in {slug}"
        rel = entry.get("path") or entry.get("fallback_snapshot")
        assert rel, f"{slug}/{artifact_id} has no file behind it"
        assert (ARTIFACTS / rel).exists(), f"{rel} is missing from artifacts/"


def test_the_guard_would_catch_a_regression():
    """A value nobody could find must fail, or this file proves nothing."""
    slug, key, _, named, artifacts = CASES[0]
    invented = "sausage-flavoured-credential"
    found = [aid for aid in named
             if invented in (text_of(artifacts, aid) or "")]
    assert not found
