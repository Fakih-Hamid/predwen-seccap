import os
import sys

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.auth import reset_rate_limits  # noqa: E402
from app.models import Team, aware, db  # noqa: E402
from app.state import create_session  # noqa: E402

FACILITATOR_PASSWORD = "rehearsal-only"


@pytest.fixture()
def app():
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
    })
    with application.app_context():
        db.create_all()
        reset_rate_limits()
        yield application
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def event(app):
    return create_session(app.config, title="Test event", code="TESTAA")


class Participant:
    """A joined member, with the CSRF token its browser would be holding."""

    def __init__(self, client, csrf, team_code, nickname):
        self.client = client
        self.csrf = csrf
        self.team_code = team_code
        self.nickname = nickname

    def post(self, url, payload):
        return self.client.post(url, json=dict(payload, csrf_token=self.csrf))

    def get(self, url):
        return self.client.get(url)


def csrf_of(client):
    """Mint the token the way a browser does: by loading a page first."""
    client.get("/join")
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def team_of(event, display_name="Team 1"):
    return Team.query.filter_by(session_id=event.id, display_name=display_name).one()


def code_of(event, display_name="Team 1"):
    return team_of(event, display_name).code


def finish_all_grading(app, event, facilitator, points=0):
    from app.scoring import required_rubric_items

    for item in required_rubric_items(app.config["CONTENT_DIR"]):
        for team in event.teams:
            facilitator.post("/facilitator/api/grade", {
                "team_id": team.id, "mission_slug": item["mission_slug"],
                "rubric_key": item["key"], "points": points,
                "reason": "test"})


def open_mission(facilitator, slug, closing_earlier=True):
    from app import missions as content

    if closing_earlier:
        for m in content.ordered_missions(facilitator.client.application.config
                                          ["CONTENT_DIR"]):
            if m["slug"] == slug:
                break
            facilitator.post(f"/facilitator/mission/{m['slug']}/close", {})
    return facilitator.post(f"/facilitator/mission/{slug}/open", {})


def wind_forward(app, event, slug, seconds=1200):
    import datetime

    from app.models import Mission, db

    with app.app_context():
        row = Mission.query.filter_by(session_id=event.id, slug=slug).one()
        assert row.opened_at is not None, (
            f"{slug} has not been opened — wind_forward has no clock to move")
        row.opened_at = aware(row.opened_at) - datetime.timedelta(seconds=seconds)
        db.session.commit()


def open_all_sources(app, event, slug, team="Team 1"):
    from app import missions as content
    from app.state import mission_row, open_pivot

    with app.app_context():
        row = mission_row(event, slug)
        target = team_of(event, team)
        definition = content.get_mission(app.config["CONTENT_DIR"], slug)
        for entry in (definition or {}).get("artifacts") or []:
            open_pivot(event, target, row, entry["id"])


def join(app, event, team="Team 1", nickname="analyst"):
    client = app.test_client()
    token = csrf_of(client)
    code = code_of(event, team)
    res = client.post("/join", data={
        "csrf_token": token, "session_code": event.code,
        "team_code": code, "nickname": nickname,
    })
    assert res.status_code in (302, 303), res.data[:400]
    return Participant(client, token, code, nickname)


@pytest.fixture()
def facilitator(app):
    client = app.test_client()
    client.get("/facilitator/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    res = client.post("/facilitator/login",
                      data={"csrf_token": token, "password": FACILITATOR_PASSWORD})
    assert res.status_code in (302, 303)
    return Participant(client, token, None, "facilitator")

ARCHIVE_SKIPS = (
    "tests/test_check_external.py::test_set_pending_flips_only_what_it_is_given",
    "tests/test_check_external.py::test_it_never_touches_a_retired_resource",
    "tests/test_check_external.py::test_it_is_a_no_op_on_something_already_pending",
    "tests/test_check_external.py::test_it_changes_exactly_one_line_per_resource",
    "tests/test_external.py::test_a_published_launch_point_shows_the_brief_and_not_the_file",
    "tests/test_external.py::test_there_is_no_offline_mode_to_declare",
    "tests/test_external.py::test_the_last_two_sources_are_published_and_serve_nothing",
    "tests/test_external.py::test_no_launch_point_can_be_switched_to_its_saved_copy",
    "tests/test_full_rehearsal.py::test_the_room_is_online_and_there_is_no_way_to_make_it_otherwise",
    "tests/test_hardening.py::test_the_manifest_snapshot_still_publishes_its_own_hash",
    "tests/test_hardening.py::test_a_published_payload_is_fetched_from_the_web_and_not_from_here",
    "tests/test_hardening.py::test_the_photograph_is_fetched_from_the_web_so_exiftool_reads_the_original",
    "tests/test_hardening.py::test_the_download_refuses_while_the_live_resource_is_up",
    "tests/test_hardening.py::test_a_published_source_hands_over_no_bytes",
    "tests/test_original_untouched.py::test_the_original_repository_is_unchanged",
    "tests/test_sources_are_external.py::test_the_dns_page_does_not_print_the_addresses",
    "tests/test_sources_are_external.py::test_it_shows_the_commands_instead",
    "tests/test_sources_are_external.py::test_the_bytes_are_refused_too",
    "tests/test_sources_are_external.py::test_they_hand_over_nothing_now_that_they_are_published",
    "tests/test_sources_are_external.py::test_the_two_new_resources_are_declared_and_have_their_file",
)


def _archive_mode():
    import re
    manifest = os.path.join(os.path.dirname(__file__), "..", "content",
                            "external_resources.yaml")
    with open(manifest, encoding="utf-8") as fh:
        block = re.search(r"- key: m1_repository\n(?:    .*\n|\n)*?    status: (\w+)",
                          fh.read())
    return bool(block) and block.group(1) == "pending"


def pytest_collection_modifyitems(config, items):
    if not _archive_mode():
        return
    skip = pytest.mark.skip(reason="pins the live-event configuration; this "
                                   "archive ships with its hosted resources pending")
    for item in items:
        if item.nodeid.split("[", 1)[0] in ARCHIVE_SKIPS:
            item.add_marker(skip)
