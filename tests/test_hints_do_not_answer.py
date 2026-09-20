import pathlib
import re

import pytest
import yaml

CONTENT = pathlib.Path(__file__).resolve().parents[1] / "content" / "missions"
LANGS = ("en", "ja")

def graded_values(mission):
    """Every string a team could be marked right for producing."""
    out = {}
    for question in mission.get("questions") or []:
        values = set()
        validator = question.get("validator") or {}
        for key in ("accept", "accept_any"):
            for value in validator.get(key) or []:
                if isinstance(value, str):
                    values.add(value)
        for option in question.get("options") or []:
            if not option.get("correct"):
                continue
            for lang in LANGS:
                label = (option.get("label") or {}).get(lang)
                if label and len(label) <= 60:
                    values.add(label)
        if values:
            out[question["key"]] = values
    return out


def os_text(label):
    if isinstance(label, dict):
        return " ".join(v for v in label.values() if isinstance(v, str))
    return label or ""


def ladders(mission):
    yield "mission", mission.get("hints") or []
    for artifact in mission.get("artifacts") or []:
        if artifact.get("hints"):
            yield artifact["id"], artifact["hints"]


def how_to_texts(mission):
    for artifact in mission.get("artifacts") or []:
        block = artifact.get("how_to")
        if not block:
            continue
        scope = f"{artifact['id']}/how_to"
        for step in block.get("steps") or []:
            for lang in LANGS:
                yield scope, lang, (step or {}).get(lang, "") or ""
        for lang in LANGS:
            yield scope, lang, (block.get("no_install") or {}).get(lang, "") or ""
        for command in block.get("commands") or []:
            yield f"{scope}/cmd", "-", command.get("cmd") or ""


def missions():
    for path in sorted(CONTENT.glob("*.yaml")):
        tree = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if tree.get("questions") and tree.get("kind", "mission") == "mission":
            yield path.name, tree


MISSIONS = list(missions())


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_hint_contains_an_accepted_answer(name, mission):
    keys = graded_values(mission)
    offences = []
    for scope, rungs in ladders(mission):
        for rung in rungs:
            for lang in LANGS:
                text = (rung.get("text") or {}).get(lang, "")
                low = text.lower()
                for question, values in keys.items():
                    for value in values:
                        if value.lower() in low:
                            offences.append(
                                f"{scope}/{rung['id']} [{lang}] gives away "
                                f"{question}: {value!r}")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_how_to_block_contains_an_accepted_answer(name, mission):
    """The same check as the rungs, on the text nobody has to press for."""
    keys = graded_values(mission)
    offences = []
    for scope, lang, text in how_to_texts(mission):
        low = text.lower()
        for question, values in keys.items():
            for value in values:
                if value.lower() in low:
                    offences.append(f"{scope} [{lang}] gives away {question}: "
                                    f"{value!r}  in: {text[:70]!r}")
    assert not offences, "\n".join(offences)


POINTING_AT_THE_FINDING = (
    "the last row", "the last line", "the last lines", "exactly one",
    "not everything here", "note the remark", "are harmless", "is harmless",
    "最終行", "最後の2行", "無害",
)

MAY_SAY_HARMLESS = {"payload"}


def page_text(mission):
    """Every string a source page prints before anybody asks for help."""
    for artifact in mission.get("artifacts") or []:
        for field in ("title", "note", "expected_pivot"):
            for lang in LANGS:
                value = (artifact.get(field) or {}).get(lang)
                if value:
                    yield f"{artifact['id']}/{field}", lang, value


def context_of(mission):
    out = " ".join([
        (mission.get("narrative") or {}).get(lang, "") for lang in LANGS] + [
        (mission.get("first_step") or {}).get(lang, "") for lang in LANGS])
    return out.lower()


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_source_card_contains_an_accepted_answer(name, mission):
    """The same check as the rungs and the how_to blocks, on the card itself."""
    keys = graded_values(mission)
    context = context_of(mission)
    offences = []
    for scope, lang, text in page_text(mission):
        low = text.lower()
        for question, values in keys.items():
            for value in values:
                if value.lower() in low and value.lower() not in context:
                    offences.append(f"{scope} [{lang}] gives away {question}: "
                                    f"{value!r}  in: {text[:70]!r}")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_source_card_points_at_the_finding(name, mission):
    offences = []
    for scope, lang, text in list(page_text(mission)) + list(how_to_texts(mission)):
        low = text.lower()
        exempt = scope.split("/")[0] in MAY_SAY_HARMLESS
        for tell in POINTING_AT_THE_FINDING:
            if tell not in low:
                continue
            if exempt and tell in ("are harmless", "is harmless", "無害"):
                continue
            offences.append(f"{scope} [{lang}]: {tell!r} — that belongs on "
                            f"the orientation rung, not on the card")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_every_how_to_block_covers_windows(name, mission):
    thin = []
    for artifact in mission.get("artifacts") or []:
        block = artifact.get("how_to")
        if not block:
            continue
        commands = block.get("commands") or []
        if not commands:
            continue      # steps-only blocks are prose; nothing to install.
        labels = " ".join(os_text(c.get("os")) for c in commands).lower()
        if "windows" not in labels and not block.get("no_install"):
            thin.append(artifact["id"])
    assert not thin, (
        f"how_to blocks with no Windows command and no no-install route: {thin}")


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_hint_contains_an_attack_technique_id(name, mission):
    offences = []
    for scope, rungs in ladders(mission):
        for rung in rungs:
            for lang in LANGS:
                found = re.findall(r"\bT1\d{3}(?:\.\d{3})?\b",
                                   (rung.get("text") or {}).get(lang, ""))
                if found:
                    offences.append(f"{scope}/{rung['id']} [{lang}]: {found}")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_hint_contains_a_full_hash_or_a_timestamp_answer(name, mission):
    """The two shapes that are always a value and never a direction."""
    offences = []
    for scope, rungs in ladders(mission):
        for rung in rungs:
            for lang in LANGS:
                text = (rung.get("text") or {}).get(lang, "")
                if re.search(r"\b[0-9a-f]{32,}\b", text):
                    offences.append(f"{scope}/{rung['id']} [{lang}]: a full hash")
                if re.search(r"\b\d{2}:\d{2}:\d{2}Z?\b", text):
                    offences.append(f"{scope}/{rung['id']} [{lang}]: a clock time")
    assert not offences, "\n".join(offences)


def vocabulary(text):
    return set(re.findall(r"[a-z]{4,}", (text or "").lower()))


def overlap(a, b):
    va, vb = vocabulary(a), vocabulary(b)
    if not va or not vb:
        return 0.0
    return len(va & vb) / min(len(va), len(vb))


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_a_source_ladder_never_repeats_the_page_or_itself(name, mission):
    offences = []
    for artifact in mission.get("artifacts") or []:
        if not artifact.get("external_url"):
            continue
        rungs = {h["level"]: h for h in (artifact.get("hints") or [])}
        for lang in LANGS:
            note = (artifact.get("note") or {}).get(lang, "")
            pivot = (artifact.get("expected_pivot") or {}).get(lang, "")
            first = ((rungs.get("orientation") or {}).get("text") or {}).get(lang, "")
            second = ((rungs.get("recovery") or {}).get("text") or {}).get(lang, "")
            if lang != "en":
                continue                    # word-boundary overlap is Latin-only
            for label, a, b in (("rung 1 repeats the note", note, first),
                                ("rung 2 repeats the note", note, second),
                                ("rung 2 repeats rung 1", first, second),
                                ("rung 1 repeats the pivot", pivot, first)):
                score = overlap(a, b)
                if score > 0.5:
                    offences.append(f"{artifact['id']}: {label} "
                                    f"({int(score * 100)}%)")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_source_ladder_promises_a_saved_copy(name, mission):
    offences = []
    for scope, rungs in ladders(mission):
        for rung in rungs:
            for lang in LANGS:
                text = (rung.get("text") or {}).get(lang, "").lower()
                for claim in ("saved copy", "further down this page",
                              "保存済みコピー", "このページ下部"):
                    if claim in text:
                        offences.append(f"{scope}/{rung['id']} [{lang}]: {claim}")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_every_mission_ladder_escalates_through_the_four_jobs(name, mission):
    levels = [rung["level"] for rung in mission.get("hints") or []]
    assert levels == ["orientation", "pivot", "tool", "method", "recovery"], levels


MINIMUM_RUNG = {"en": 40, "ja": 16}


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_every_rung_says_something_and_costs_nothing(name, mission):
    for scope, rungs in ladders(mission):
        for rung in rungs:
            assert rung.get("cost", 0) == 0, (scope, rung["id"])
            for lang in LANGS:
                text = (rung.get("text") or {}).get(lang, "")
                assert len(text) > MINIMUM_RUNG[lang], (scope, rung["id"],
                                                        lang, text)


@pytest.mark.parametrize("name,mission", MISSIONS,
                         ids=[n for n, _ in MISSIONS])
def test_no_two_rungs_of_one_ladder_say_the_same_thing(name, mission):
    for scope, rungs in ladders(mission):
        seen = []
        for rung in rungs:
            if rung["level"] == "recovery":
                continue
            words = set(re.findall(r"[a-z]{5,}",
                                   (rung.get("text") or {}).get("en", "").lower()))
            for earlier_id, earlier in seen:
                shared = words & earlier
                overlap = len(shared) / max(1, min(len(words), len(earlier)))
                assert overlap < 0.6, (
                    f"{scope}: {rung['id']} repeats {earlier_id} "
                    f"({int(overlap * 100)}% shared: {sorted(shared)[:8]})")
            seen.append((rung["id"], words))


def test_a_stuck_team_can_always_reach_the_last_rung(app, event, facilitator):
    """No waiting and no penalty: five presses, five hints, zero points lost."""
    from app.models import ScoreEvent, Team

    slug = "digital-footprint"
    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    from .conftest import join
    p = join(app, event, "Team 1", "kenji")

    definition_hints = [h["id"] for h in yaml.safe_load(
        (CONTENT / "m1-digital-footprint.yaml").read_text(encoding="utf-8")
    )["hints"]]

    for hint_id in definition_hints:
        r = p.post("/api/hint", {"mission_slug": slug, "hint_id": hint_id})
        if r.status_code == 409:
            assert p.post("/api/hint/next", {"mission_slug": slug}).get_json()["ok"]
            r = p.post("/api/hint", {"mission_slug": slug, "hint_id": hint_id})
        assert r.get_json().get("hint"), (hint_id, r.get_json())

    with app.app_context():
        team = Team.query.filter_by(session_id=event.id,
                                    display_name="Team 1").one()
        charged = ScoreEvent.query.filter_by(team_id=team.id,
                                             source="hint").all()
        assert all(row.points == 0 for row in charged), \
            [(r.category, r.points) for r in charged]
