import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "seed_demo.py"

NAMES = ["Team Kitsune", "Team Raijin", "Team Tanuki", "Team Koi",
         "Team Tsubasa", "Team Tengu"]


def seed_source():
    return SEED.read_text(encoding="utf-8")


def test_the_six_names_are_what_the_seed_script_sets():
    source = (ROOT / "app" / "state.py").read_text(encoding="utf-8")
    match = re.search(r"TEAM_NAMES = \[(.*?)\]", source, re.S)
    assert match, "app/state.py no longer declares TEAM_NAMES"
    names = re.findall(r'"([^"]+)"', match.group(1))
    assert names == NAMES, names
    assert "apply_table_plan" in seed_source(), "seed_demo.py stopped using the plan"


def test_a_session_recreated_from_the_console_keeps_the_table_names(
        app, event, facilitator):
    from app.models import EventSession, Team

    facilitator.post("/facilitator/session/reset", {})
    res = facilitator.client.post("/facilitator/session", data={
        "csrf_token": facilitator.csrf, "title": "Second run", "teams": "6"})
    assert res.status_code in (302, 303), res.data[:300]

    with app.app_context():
        ev = EventSession.query.order_by(EventSession.id.desc()).first()
        assert ev.id != event.id and ev.title == "Second run"
        teams = Team.query.filter_by(session_id=ev.id).order_by(Team.id).all()
        assert [t.display_name for t in teams] == NAMES
        assert [t.capacity for t in teams] == [4, 4, 4, 5, 5, 5]


def test_there_is_one_name_per_fixed_code():
    from app.state import DEFAULT_TEAM_COUNT, FIXED_TEAM_CODES

    assert len(NAMES) == len(FIXED_TEAM_CODES) == DEFAULT_TEAM_COUNT


def test_the_names_can_be_overridden_without_editing_the_script():
    assert '--names' in seed_source()


def test_the_engine_still_falls_back_to_a_numbered_name(app, event):
    from app.models import Team
    from app.state import add_team

    with app.app_context():
        added = add_team(event)
        assert added.display_name == "Team 7"
        assert Team.query.filter_by(session_id=event.id).count() == 7
