import hashlib
import io
import json
import os
import pathlib
import re

from app import missions as content, ui

from .conftest import join, open_all_sources

TOKEN_AUDIT_HASH = "173daab1c9ad20ce67e03ff35b2151aba5feb1598a84c90909f5b93f040a6724"

BANNED = re.compile(
    r"training[_ -]?notice|training[_ ]?note|Controlled SECCAP"
    r"|synthetic|fictional|malicious simulation|real_malware"
    r"|no real (person|malware|sample|systems|data|endpoint)"
    r"|training (persona|infrastructure|environment|report|snapshot|artifact|log|data)"
    r"|架空|合成データ|演習用",
    re.I)

ALLOWED = {
    "artifacts/digital-footprint/username_collision.json": ["photo.invalid"],
}


def cd(app):
    return app.config["CONTENT_DIR"]


def _artifact_files(app):
    root = app.config["ARTIFACT_DIR"]
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith((".png", ".jpg", ".bin")):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            yield rel, io.open(path, encoding="utf-8", errors="replace").read()


def test_no_participant_artifact_declares_itself_an_exercise(app):
    offenders = []
    for rel, text in _artifact_files(app):
        allowed = ALLOWED.get("artifacts/" + rel, [])
        for m in BANNED.finditer(text):
            line = text[:m.start()].count("\n") + 1
            if any(a in text.splitlines()[line - 1] for a in allowed):
                continue
            offenders.append("%s:%d %r" % (rel, line, m.group(0)))
    assert not offenders, offenders


def test_no_artifact_carries_a_training_notice_field(app):
    for rel, text in _artifact_files(app):
        if not rel.endswith(".json"):
            continue
        data = json.loads(text)
        if isinstance(data, dict):
            assert "training_notice" not in data, rel
            assert "training_note" not in data, rel


def test_no_participant_string_in_the_content_declares_itself_an_exercise(app):
    """Mission and UI prose, in both languages."""
    offenders = []

    def walk(node, path):
        if isinstance(node, dict):
            if isinstance(node.get("ja"), str) and "en" in node:
                for lang in ("ja", "en"):
                    value = node.get(lang)
                    if isinstance(value, str) and BANNED.search(value):
                        offenders.append("%s (%s): %r" % (path, lang, value[:70]))
                return
            for k, v in node.items():
                walk(v, path + "/" + str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))

    for slug, mission in content.load_missions(cd(app)).items():
        walk(mission, slug)
    import yaml
    walk(yaml.safe_load(io.open(os.path.join(cd(app), "ui.yaml"), encoding="utf-8")),
         "ui.yaml")
    assert not offenders, offenders


def test_mission_three_warns_without_breaking_the_case(app):
    """The evidence pack now warns in character instead of disclaiming."""
    m3 = content.load_missions(cd(app))["threat-intelligence"]
    pack = next(a for a in m3["artifacts"] if a["id"] == "evidence-pack")
    en = content.tx(pack["safety_notice"], "en")
    ja = content.tx(pack["safety_notice"], "ja")
    assert "untrusted evidence" in en and "Do not execute" in en
    assert "static analysis" in en and "hash-only" in en
    assert "実行せず" in ja and "静的解析" in ja

    sandbox = next(a for a in m3["artifacts"] if a["id"] == "sandbox")
    sen = content.tx(sandbox["safety_notice"], "en")
    assert "Do not download or run a sample" in sen
    assert "not a real sample" not in sen


def test_the_token_audit_is_byte_identical_to_the_canonical_copy(app):
    path = os.path.join(app.config["ARTIFACT_DIR"],
                        "digital-footprint", "token_audit.json")
    raw = open(path, "rb").read()
    assert hashlib.sha256(raw).hexdigest() == TOKEN_AUDIT_HASH


def test_the_virustotal_snapshot_keeps_its_analytical_limits(app):
    path = os.path.join(app.config["ARTIFACT_DIR"],
                        "threat-intelligence", "virustotal_lookup.json")
    data = json.loads(io.open(path, encoding="utf-8").read())
    assert "training_notice" not in data
    assert "may differ" in data["warning"]
    assert data["captured"].endswith("Z")
    denied = " ".join(data["what_this_does_not_support"]).lower()
    assert "safe" in denied and "malicious" in denied
    assert "not an observation of behaviour" in denied or "behaviour" in denied


def test_the_payload_and_its_hash_were_not_touched(app):
    path = os.path.join(app.config["ARTIFACT_DIR"],
                        "infrastructure", "SakuraVPNUpdate_4.2.1.bin")
    raw = open(path, "rb").read()
    assert len(raw) == 711
    assert hashlib.sha256(raw).hexdigest() == \
        "ff749df77076e1243eb3c8a3b8bb049d2e7913125532b6ab2bf0b8299ef8ea31"
