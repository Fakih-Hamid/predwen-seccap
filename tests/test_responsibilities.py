import json
import pathlib
import re

import pytest
import yaml

from .conftest import join

ROOT = pathlib.Path(__file__).resolve().parents[1]
SLUG = "digital-footprint"
SPECIALISMS = ("navigator", "osint", "infra", "cti", "evidence")
LABELS = {
    "en": ("Navigator", "OSINT Analyst", "Infrastructure Analyst",
           "CTI Analyst", "Evidence Lead / Presenter"),
    "ja": ("ナビゲーター", "OSINT アナリスト", "インフラアナリスト",
           "CTI アナリスト", "エビデンス担当／発表担当"),
}
SURFACES = ("/join", "/briefing", "/team", f"/mission/{SLUG}")

ROLE_MARKUP = ("rolelbl", "current_role", 'data-role')


def speak(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang


def open_mission(facilitator, slug=SLUG):
    facilitator.post(f"/facilitator/mission/{slug}/open", {})


def test_no_participant_surface_asks_anybody_to_choose(app, event, facilitator):
    """No picker, no dropdown, no endpoint."""
    p = join(app, event, "Team 1", "kenji")
    for path in SURFACES:
        html = p.get(path).get_data(as_text=True)
        assert "data-rolepicker" not in html, path
        assert "/api/role" not in html, path
        assert 'name="role"' not in html, path


def test_the_role_endpoint_is_gone(app, event, facilitator):
    p = join(app, event, "Team 1", "kenji")
    assert p.post("/api/role", {"role": "navigator"}).status_code == 404
    assert p.post("/api/rotate", {}).status_code == 404


def test_joining_needs_a_team_a_code_and_a_nickname(app, event):
    client = app.test_client()
    html = client.get("/join").get_data(as_text=True)
    form = html.split('<form method="post" class="panel">', 1)[1].split("</form>", 1)[0]
    names = set(re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', form))
    assert names == {"csrf_token", "team_id", "team_code", "nickname"}, names
    assert "session_code" not in names
    assert "role" not in names


def seats_of(html):
    return re.findall(r'<span class="seat[^"]*">', html)


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_waiting_screen_shows_name_and_presence_only(app, event, facilitator,
                                                         lang):
    seats = [join(app, event, "Team 1", f"four-{i}") for i in range(4)]
    p = seats[0]
    speak(p.client, lang)
    html = p.get(f"/mission/{SLUG}").get_data(as_text=True)
    assert "mission.not_open" not in html          # the key must not leak
    body = html.split("</header>", 1)[-1]

    for nick in (f"four-{i}" for i in range(4)):
        assert nick in body, nick
    for marker in ROLE_MARKUP:
        assert marker not in body, marker
    for label in LABELS["en"] + LABELS["ja"]:
        assert label not in body, label
    assert len(seats_of(html)) == 4


def test_the_waiting_screen_body_says_responsibilities_are_optional(app, event,
                                                                    facilitator):
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, "en")
    assert ("Responsibilities are optional and may be shared."
            in p.get(f"/mission/{SLUG}").get_data(as_text=True))
    speak(p.client, "ja")
    assert ("作業分担は任意で、同じ作業を複数人で担当しても構いません。"
            in p.get(f"/mission/{SLUG}").get_data(as_text=True))


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_open_mission_page_renders_no_role_either(app, event, facilitator,
                                                      lang):
    open_mission(facilitator)
    seats = [join(app, event, "Team 1", f"four-{i}") for i in range(4)]
    p = seats[0]
    speak(p.client, lang)
    body = p.get(f"/mission/{SLUG}").get_data(as_text=True).split("</header>", 1)[-1]
    for marker in ROLE_MARKUP:
        assert marker not in body, marker
    for label in LABELS["en"] + LABELS["ja"]:
        assert label not in body, label
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert len(state["members"]) == 4
    for member in state["members"]:
        assert "role" not in member and "current_role" not in member, member


def test_no_template_reads_the_stored_role():
    """The template is the one place the source scan below did not look."""
    templates = ROOT / "app" / "templates"
    offenders = [p.name for p in templates.rglob("*.html")
                 if "current_role" in p.read_text(encoding="utf-8")
                 .replace("{#", "\x00").split("\x00")[0]  # code before any comment
                 or re.search(r"\{\{[^}]*current_role", p.read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_no_member_payload_carries_a_responsibility(app, event, facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")

    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    for member in state["members"]:
        assert "role" not in member, member

    p.post("/api/observation", {"mission_slug": SLUG, "text": "the updater"})
    obs = p.get(f"/api/observations?mission_slug={SLUG}").get_json()
    for row in obs["observations"]:
        assert "role" not in row, row


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_guide_offers_the_five_without_assigning_them(app, event,
                                                          facilitator, lang):
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    html = p.get("/briefing").get_data(as_text=True)

    for label in LABELS[lang]:
        assert label in html, (lang, label)
    heading = "Ways to split the work (optional)" if lang == "en" \
        else "作業の分け方（任意）"
    assert heading in html
    assert "The five roles" not in html
    assert "5つの役割" not in html


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_guide_says_an_empty_direction_costs_nothing(app, event,
                                                         facilitator, lang):
    """A team of four must not read the list as a person short."""
    p = join(app, event, "Team 1", "kenji")
    speak(p.client, lang)
    html = p.get("/briefing").get_data(as_text=True)

    if lang == "en":
        assert "regardless of team size" in html
        assert "material needed for every question" in html
        assert "affects neither your score nor your progress" not in html
    else:
        assert "チーム人数に関係なく" in html
        assert "すべての設問に必要な資料を利用できます" in html
        assert "点数にも進行にも影響しません" not in html


def test_the_strings_file_still_defines_the_five_as_content():
    roles = yaml.safe_load((ROOT / "content" / "ui.yaml").read_text(
        encoding="utf-8"))["roles"]
    for name in SPECIALISMS:
        assert roles[name]["ja"] and roles[name]["en"], name
        assert roles[f"{name}_desc"]["ja"] and roles[f"{name}_desc"]["en"], name
    for gone in ("change", "changed", "free_hint", "rotate", "rotated",
                 "coordinator", "coordinator_desc"):
        assert gone not in roles, gone


@pytest.fixture
def four_and_five(app, event, facilitator):
    """One team of four and one of five, both real, through the join form."""
    for slug in ("digital-footprint", "fake-infrastructure",
                 "threat-intelligence"):
        open_mission(facilitator, slug)
    four = [join(app, event, "Team 1", f"four-{i}") for i in range(4)]
    five = [join(app, event, "Team 2", f"five-{i}") for i in range(5)]
    return four, five


def test_a_team_of_four_reaches_every_participant_screen(four_and_five):
    four, _ = four_and_five
    p = four[0]
    for path in ("/team", "/briefing", "/evidence",
                 f"/mission/{SLUG}", "/artifacts/commit", "/final",
                 "/collective-intel", "/scoreboard", "/presentation"):
        assert p.get(path).status_code == 200, path


def test_a_team_of_four_answers_evidences_and_locks(four_and_five, app, event):
    four, _ = four_and_five
    p = four[0]

    assert p.post("/api/submission", {"mission_slug": SLUG,
                                      "question_key": "m1_key_id",
                                      "answer": "SR-REL-2019"}
                  ).get_json()["ok"]
    assert p.post("/api/evidence", {"mission_slug": SLUG,
                                    "question_key": "m1_key_id",
                                    "artifact_id": "commit",
                                    "excerpt": "read it here"}
                  ).get_json()["ok"]
    assert p.post("/api/hint/next", {"mission_slug": SLUG}).get_json()["ok"]
    assert p.post("/api/lock", {"mission_slug": SLUG}).get_json()["ok"]

    progress = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert progress["progress"]["answered"] == 1
    assert progress["progress"]["with_evidence"] == 1


def test_four_and_five_score_identically_on_identical_work(four_and_five, app,
                                                           event, facilitator):
    four, five = four_and_five
    answers = {"m1_key_id": "SR-REL-2019",
               "m1_flag": "ALLOW_UNSIGNED_RECOVERY",
               "m1_test": "assertions_removed"}

    for p in (four[0], five[0]):
        for key, value in answers.items():
            p.post("/api/submission", {"mission_slug": SLUG,
                                       "question_key": key, "answer": value})
            p.post("/api/evidence", {"mission_slug": SLUG, "question_key": key,
                                     "artifact_id": "commit",
                                     "excerpt": "same excerpt"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})

    scores = {}
    for name, p in (("four", four[0]), ("five", five[0])):
        scores[name] = p.get("/api/my-score").get_json()["breakdown"]
    assert scores["four"] == scores["five"], scores


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 9])
def test_any_team_size_works_and_none_is_special(app, event, facilitator, size):
    """No code path anywhere asks how many people are on a team."""
    open_mission(facilitator)
    members = [join(app, event, "Team 1", f"m{i}") for i in range(size)]

    for p in members:
        assert p.get(f"/mission/{SLUG}").status_code == 200
    first = members[0]
    assert first.post("/api/submission", {"mission_slug": SLUG,
                                          "question_key": "m1_key_id",
                                          "answer": "SR-REL-2019"}
                      ).get_json()["ok"]
    assert first.post("/api/lock", {"mission_slug": SLUG}).get_json()["ok"]


def test_nothing_in_the_engine_reads_a_responsibility():
    import app.facilitator.routes as facilitator_routes
    import app.participant.routes as participant_routes
    import app.reports as reports
    import app.scoring as scoring
    import app.state as state

    for module in (scoring, state, reports, participant_routes,
                   facilitator_routes):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        code = re.sub(r'""".*?"""', "", code, flags=re.S)
        assert "current_role" not in code, module.__name__


def _code_only(path):
    source = path.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    return re.sub(r'""".*?"""', "", code, flags=re.S)


def test_no_runtime_path_outside_the_model_reads_the_column():
    offenders = []
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        if path.name == "models.py":
            continue
        if "current_role" in _code_only(path):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders


def run_export(app, tmp_path, monkeypatch):
    """The real export script, against this test's database."""
    import importlib.util
    import json
    import sys

    script = ROOT / "scripts" / "export_results.py"
    spec = importlib.util.spec_from_file_location("export_results_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "create_app", lambda: app)
    monkeypatch.setattr(sys, "argv", ["export_results.py", "--out", str(tmp_path)])
    assert module.main() == 0
    session_file = next(pathlib.Path(tmp_path).glob("*_session.json"))
    return json.loads(session_file.read_text(encoding="utf-8"))


def test_a_four_person_team_exports_exactly_four_members_and_no_role(
        app, event, facilitator, tmp_path, monkeypatch):
    open_mission(facilitator)
    for i in range(4):
        join(app, event, "Team 1", f"four-{i}")

    doc = run_export(app, tmp_path, monkeypatch)
    team = next(t for t in doc["teams"] if t["name"] == "Team 1")
    assert sorted(m["nickname"] for m in team["members"]) == \
        [f"four-{i}" for i in range(4)]
    for member in team["members"]:
        assert set(member) == {"nickname"}, member
    text = json.dumps(doc)
    assert '"role"' not in text and "current_role" not in text


def test_the_stored_role_changes_neither_scores_nor_the_export(
        app, event, facilitator, tmp_path, monkeypatch):
    from app.models import Member, db

    open_mission(facilitator)
    one = join(app, event, "Team 1", "kenji")
    two = join(app, event, "Team 2", "aoi")
    for p in (one, two):
        p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                   "answer": "SR-REL-2019"})
        p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                                 "artifact_id": "commit", "excerpt": "same"})
    for m in Member.query.all():
        if m.nickname == "aoi":
            m.current_role = "cti"
    db.session.commit()

    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})
    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    assert (one.get("/api/my-score").get_json()["breakdown"]
            == two.get("/api/my-score").get_json()["breakdown"])

    doc = run_export(app, tmp_path, monkeypatch)
    by_name = {t["name"]: t for t in doc["teams"]}
    assert by_name["Team 1"]["score"] == by_name["Team 2"]["score"]
    assert by_name["Team 2"]["members"] == [{"nickname": "aoi"}]


def test_the_column_is_still_there_for_an_old_database(app):
    from sqlalchemy import inspect

    from app.models import Member, db

    assert "current_role" in Member.__table__.columns
    assert "current_role" in {c["name"] for c in inspect(db.engine).get_columns("members")}
