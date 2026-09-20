import pathlib
import re
import shutil

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, external
from app.models import db
from app.state import create_session

from .conftest import FACILITATOR_PASSWORD, Participant, open_all_sources

ROOT = pathlib.Path(__file__).resolve().parents[1]
SLUGS = ("digital-footprint", "fake-infrastructure", "threat-intelligence")


@pytest.fixture()
def blackout(tmp_path):
    """An application whose every external resource is declared `pending`."""
    content = tmp_path / "content"
    shutil.copytree(ROOT / "content", content)

    manifest = content / "external_resources.yaml"
    text = manifest.read_text(encoding="utf-8")
    flipped = re.sub(r"^(\s*)status: ready$", r"\1status: pending", text,
                     flags=re.MULTILINE)
    assert flipped != text
    manifest.write_text(flipped, encoding="utf-8", newline="\n")

    external.clear_cache()
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "FACILITATOR_PASSWORD_HASH": generate_password_hash(FACILITATOR_PASSWORD),
        "DEFAULT_LANG": "en",
        "ADMIN_BYPASS": False,
        "CONTENT_DIR": str(content),
    })
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
    external.clear_cache()


@pytest.fixture()
def room(blackout):
    """A seeded session and a signed-in facilitator, on the blacked-out app."""
    event = create_session(blackout.config, title="Blackout rehearsal",
                           code="DARKAA")
    client = blackout.test_client()
    client.get("/facilitator/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    response = client.post("/facilitator/login",
                           data={"csrf_token": token,
                                 "password": FACILITATOR_PASSWORD})
    assert response.status_code in (302, 303)
    return blackout, event, Participant(client, token, None, "facilitator")


def member(app, event, facilitator, slug):
    from .conftest import join, wind_forward

    facilitator.post(f"/facilitator/mission/{slug}/open", {})
    wind_forward(app, event, slug)
    open_all_sources(app, event, slug)
    return join(app, event, "Team 1", "kenji")


def definition(app, slug):
    from app import missions as content

    with app.app_context():
        return content.get_mission(app.config["CONTENT_DIR"], slug)


@pytest.mark.parametrize("slug", SLUGS)
def test_every_source_still_opens_and_shows_something(room, slug):
    app, event, facilitator = room
    p = member(app, event, facilitator, slug)

    empty = []
    for artifact in definition(app, slug)["artifacts"]:
        aid = artifact["id"]
        response = p.get(f"/artifacts/{aid}")
        assert response.status_code == 200, aid
        html = response.get_data(as_text=True)

        assert "Not published" in html, aid
        if artifact.get("run_it_yourself"):
            assert "hosts are not responding" in html, aid
            assert "can be run when the hosts are back" in html, aid
        served = ('id="tree"' in html or "pre class=\"raw\"" in html
                  or "binary_body" in html or "This is an image" in html)
        if not served:
            empty.append(aid)
    assert not empty, f"{slug}: these pages fell back to nothing: {empty}"


@pytest.mark.parametrize("slug", SLUGS)
def test_the_dead_link_is_never_offered(room, slug):
    app, event, facilitator = room
    p = member(app, event, facilitator, slug)

    for artifact in definition(app, slug)["artifacts"]:
        html = p.get(f"/artifacts/{artifact['id']}").get_data(as_text=True)
        launch = re.findall(r'<a class="btn primary" href="([^"]+)" target="_blank"',
                            html)
        assert not launch, f"{artifact['id']} still offers {launch}"


@pytest.mark.parametrize("slug", SLUGS)
def test_the_help_is_all_still_there(room, slug):
    """The ladders and the tool instructions do not depend on a network."""
    app, event, facilitator = room
    p = member(app, event, facilitator, slug)

    for artifact in definition(app, slug)["artifacts"]:
        html = p.get(f"/artifacts/{artifact['id']}").get_data(as_text=True)
        assert "data-rung" in html, artifact["id"]
        if artifact.get("how_to"):
            assert "How to work with this" in html, artifact["id"]


def test_the_bytes_are_downloadable_when_the_resource_is_not_published(room):
    app, event, facilitator = room
    p = member(app, event, facilitator, "fake-infrastructure")

    assert p.get("/artifacts/payload/download").status_code == 200
    assert p.get("/artifacts/cdn-manifest/download").status_code == 200
    assert p.get("/artifacts/dns/download").status_code == 200


@pytest.mark.parametrize("artifact_id,expected", [
    ("file-timeline", "file-timeline.csv"),
    ("network", "network-connections.csv"),
    ("persistence", "persistence.csv"),
    ("powershell-log", "powershell-operational.log"),
])
def test_the_fallback_download_is_named_what_the_commands_say(room, artifact_id,
                                                              expected):
    app, event, facilitator = room
    p = member(app, event, facilitator, "threat-intelligence")

    response = p.get(f"/artifacts/{artifact_id}/download")
    assert response.status_code == 200, artifact_id
    assert f'filename="{expected}"' in response.headers["Content-Disposition"]


def test_a_resource_with_no_filename_keeps_the_local_name(room):
    app, event, facilitator = room
    p = member(app, event, facilitator, "digital-footprint")

    disposition = p.get("/artifacts/commit/download").headers["Content-Disposition"]
    assert 'filename="commit_85902d17.txt"' in disposition


def test_a_team_can_finish_all_three_missions_and_score(room):
    """End to end with nothing reachable: answer, cite, lock, be marked."""
    from .conftest import join, wind_forward

    app, event, facilitator = room
    scored = {}

    for slug in SLUGS:
        facilitator.post(f"/facilitator/mission/{slug}/open", {})
        wind_forward(app, event, slug)
        open_all_sources(app, event, slug)
        p = join(app, event, "Team 1", f"kenji-{slug[:4]}")
        mission = definition(app, slug)

        for question in mission["questions"]:
            key = question["key"]
            assert p.post("/api/submission",
                          {"mission_slug": slug, "question_key": key,
                           "answer": "x", "confidence": "low",
                           "reasoning": "written during the blackout"}
                          ).get_json()["ok"], key
            if question.get("accepted_evidence"):
                assert p.post("/api/evidence",
                              {"mission_slug": slug, "question_key": key,
                               "artifact_id": question["accepted_evidence"][0],
                               "excerpt": "from the saved copy"}
                              ).get_json()["ok"], key

        assert p.post("/api/lock", {"mission_slug": slug}).get_json()["ok"], slug
        facilitator.post(f"/facilitator/mission/{slug}/close", {})
        scored[slug] = p

    facilitator.post("/facilitator/session/state", {"scoreboard_visible": True})
    payload = scored[SLUGS[-1]].get("/api/my-score").get_json()
    assert payload["visible"], payload
    breakdown = payload["breakdown"]
    assert breakdown["events"] > 0, breakdown
    assert breakdown["total"] > 0, breakdown
    assert set(breakdown["by_mission"]) >= set(SLUGS), breakdown["by_mission"]


def test_the_remedy_and_this_test_agree_on_what_pending_means(blackout):
    assert "pending" in external.STATUSES
    with blackout.app_context():
        entries = external.load(blackout.config["CONTENT_DIR"], force=True)
    assert entries, "the throwaway manifest did not load"
    assert not [k for k, e in entries.items() if e.get("status") == "ready"], \
        "the blackout fixture left something `ready`"
