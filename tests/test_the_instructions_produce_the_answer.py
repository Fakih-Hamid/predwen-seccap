import hashlib
import pathlib
import struct

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PHOTO = ROOT / "artifacts" / "digital-footprint" / "apac-fallback-bench.jpg"
PACKAGE = ROOT / "artifacts" / "infrastructure" / "SakuraVPNUpdate_4.2.1.bin"


def exif_segment(path):
    """The raw APP1/Exif payload of a JPEG."""
    blob = path.read_bytes()
    assert blob[:2] == b"\xff\xd8", f"{path.name} is not a JPEG"
    i = 2
    while i < len(blob) - 4 and blob[i] == 0xFF:
        marker = blob[i + 1]
        if marker in (0xD8, 0xD9, 0xDA):
            break
        length = struct.unpack(">H", blob[i + 2:i + 4])[0]
        if marker == 0xE1 and blob[i + 4:i + 10] == b"Exif\x00\x00":
            return blob[i + 10:i + 2 + length]
        i += 2 + length
    raise AssertionError(f"{path.name} has no EXIF segment at all")


def question(slug, key):
    tree = yaml.safe_load(
        (ROOT / "content" / "missions" / slug).read_text(encoding="utf-8"))
    return next(q for q in tree["questions"] if q["key"] == key)


PRESENT = {
    "artist": b"Sora Nakamura",
    "datetime": b"2026:09:01 18:14:32",
    "model": b"SR-LAB-CAM-02",
}


def test_every_option_marked_correct_is_really_in_the_file():
    exif = exif_segment(PHOTO)
    q = question("m1-digital-footprint.yaml", "m1_exif")
    correct = {o["id"] for o in q["options"] if o.get("correct")}

    assert correct == set(PRESENT), (
        f"the answer key marks {sorted(correct)} correct but this test knows "
        f"how to look for {sorted(PRESENT)} — one of them moved")
    for option_id, value in PRESENT.items():
        assert value in exif, (
            f"m1_exif accepts '{option_id}' but {value!r} is not in the "
            f"photo's EXIF. The instructions send a team to a tag that is not "
            f"there.")


def test_the_absence_that_is_the_finding_is_still_an_absence():
    exif = exif_segment(PHOTO)
    q = question("m1-digital-footprint.yaml", "m1_exif")
    distractors = {o["id"] for o in q["options"] if not o.get("correct")}
    assert "gps" in distractors

    assert exif[:2] in (b"II", b"MM")
    marker = b"\x25\x88" if exif[:2] == b"II" else b"\x88\x25"
    entries = exif[8:]
    assert marker not in entries, "the photo has gained a GPS IFD"
    assert b"GPS" not in exif


def test_the_other_distractor_is_absent_too():
    exif = exif_segment(PHOTO)
    q = question("m1-digital-footprint.yaml", "m1_exif")
    assert "serial" in {o["id"] for o in q["options"] if not o.get("correct")}
    for needle in (b"SerialNumber", b"BodySerialNumber", b"Serial"):
        assert needle not in exif, needle


def test_the_panel_names_the_file_the_command_is_run_on():
    tree = yaml.safe_load(
        (ROOT / "content" / "missions" / "m1-digital-footprint.yaml")
        .read_text(encoding="utf-8"))
    artifact = next(a for a in tree["artifacts"] if a["id"] == "photo-exif")
    commands = " ".join(c["cmd"] for c in artifact["how_to"]["commands"])

    assert PHOTO.name in commands, (
        f"the how_to runs exiftool on something other than {PHOTO.name}")


def test_the_hash_command_produces_the_accepted_answer():
    computed = hashlib.sha256(PACKAGE.read_bytes()).hexdigest()

    q = question("m2-fake-infrastructure.yaml", "m2_hash")
    accepted = [a.lower() for a in q["validator"]["accept"]]
    assert computed in accepted, (
        f"sha256sum prints {computed}, the answer key accepts {accepted}")


def test_it_also_matches_what_the_manifest_declares():
    import json

    computed = hashlib.sha256(PACKAGE.read_bytes()).hexdigest()
    manifest = json.loads(
        (ROOT / "artifacts" / "infrastructure" / "cdn_manifest.json")
        .read_text(encoding="utf-8"))

    declared = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if "sha256" in key.lower() and isinstance(value, str):
                    declared.append(value.lower())
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(manifest)
    assert declared, "the CDN manifest declares no hash at all"
    assert all(d == computed for d in declared), (declared, computed)


@pytest.mark.parametrize("fragment", ["sha256sum", "Get-FileHash",
                                      "certutil -hashfile", "shasum -a 256"])
def test_the_panel_offers_a_hash_command_for_each_platform(fragment):
    tree = yaml.safe_load(
        (ROOT / "content" / "missions" / "m2-fake-infrastructure.yaml")
        .read_text(encoding="utf-8"))
    artifact = next(a for a in tree["artifacts"] if a["id"] == "payload")
    commands = " ".join(c["cmd"] for c in artifact["how_to"]["commands"])
    assert fragment in commands


def test_the_hash_commands_name_the_file_that_is_served():
    tree = yaml.safe_load(
        (ROOT / "content" / "missions" / "m2-fake-infrastructure.yaml")
        .read_text(encoding="utf-8"))
    artifact = next(a for a in tree["artifacts"] if a["id"] == "payload")
    for command in artifact["how_to"]["commands"]:
        assert PACKAGE.name in command["cmd"], command
