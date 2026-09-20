import json

import pytest

from app import external, missions as content
from app.artifacts import snapshot_path
from app.models import EventSession, Team, db

from .conftest import join, open_all_sources, wind_forward

SLUG = "digital-footprint"


def cd(app):
    return app.config["CONTENT_DIR"]


def ad(app):
    return app.config["ARTIFACT_DIR"]


def test_the_manifest_loads_and_validates(app):
    assert external.validate(cd(app), ad(app)) == []
    assert external.load(cd(app)), "the manifest is not empty"


def test_every_placeholder_a_mission_uses_is_declared(app):
    missions = list(content.load_missions(cd(app)).values())
    assert external.validate_references(cd(app), missions) == []


def test_no_content_file_carries_a_literal_url(app):
    for m in content.load_missions(cd(app)).values():
        for a in m.get("artifacts") or []:
            raw = a.get("external_url")
            if raw is None:
                continue
            assert external.placeholder_key(raw), f"{m['slug']}/{a['id']}: {raw}"


def test_nothing_is_marked_ready_without_a_url(app):
    for key, entry in external.load(cd(app)).items():
        if entry.get("status") == "ready":
            assert entry.get("url"), key


def test_every_external_artifact_has_a_local_snapshot(app):
    """The graded path never depends on somebody else's uptime."""
    for m in content.load_missions(cd(app)).values():
        for a in m.get("artifacts") or []:
            if not a.get("external_url"):
                continue
            rel = snapshot_path(a)
            assert rel, f"{m['slug']}/{a['id']} has no snapshot"
            from app.artifacts import resolve
            assert resolve(ad(app), rel), f"{m['slug']}/{a['id']} -> {rel}"


@pytest.fixture()
def manifest_dir(tmp_path):
    (tmp_path / "external_resources.yaml").write_text(u"""
version: "1.0"
resources:
  - key: not_built_yet
    tool_name: Web browser
    resource_type: first_party
    status: pending
    url: null
    fallback_snapshot: digital-footprint/commit_85902d17.txt
  - key: written_down_not_checked
    tool_name: Web browser
    resource_type: first_party
    status: pending
    url: "https://example.invalid/"
    fallback_snapshot: digital-footprint/commit_85902d17.txt
  - key: live
    tool_name: Web browser
    resource_type: first_party
    status: ready
    url: "https://example.invalid/live"
    fallback_snapshot: digital-footprint/commit_85902d17.txt
  - key: taken_down
    tool_name: Web browser
    resource_type: first_party
    status: retired
    url: "https://example.invalid/gone"
    fallback_snapshot: digital-footprint/commit_85902d17.txt
""", encoding="utf-8")
    external.clear_cache()
    yield str(tmp_path)
    external.clear_cache()


def test_a_pending_resource_resolves_to_no_link(manifest_dir):
    resolved = external.resolve(manifest_dir, "${not_built_yet}")
    assert resolved["status"] == "pending"
    assert resolved["published"] is False
    assert resolved["url"] is None


def test_a_url_written_down_but_not_checked_is_still_not_a_link(manifest_dir):
    resolved = external.resolve(manifest_dir, "${written_down_not_checked}")
    assert resolved["published"] is False
    assert resolved["url"] is None


def test_a_ready_resource_is_the_only_one_that_launches(manifest_dir):
    live = external.resolve(manifest_dir, "${live}")
    assert live["published"] is True
    assert live["url"] == "https://example.invalid/live"

    retired = external.resolve(manifest_dir, "${taken_down}")
    assert retired["published"] is False
    assert retired["url"] is None


def test_the_manifest_cache_is_keyed_on_the_directory(app, manifest_dir):
    """Two content directories, two manifests, no bleed between them."""
    assert set(external.load(manifest_dir)) == {
        "not_built_yet", "written_down_not_checked", "live", "taken_down"}
    assert "m1_repository" in external.load(cd(app))
    assert "m1_repository" not in external.load(manifest_dir)


def test_an_unknown_placeholder_is_not_launchable(app):
    resolved = external.resolve(cd(app), "${nothing_declares_this}")
    assert resolved["published"] is False
    assert resolved["unknown"] is True


def test_only_first_party_resources_are_embeddable(app):
    """Not authorable. A YAML must not be able to put crt.sh in a frame."""
    for key, entry in external.load(cd(app)).items():
        resolved = external.resolve(cd(app), "${%s}" % key)
        if entry.get("resource_type") == "first_party":
            assert resolved["embeddable"] is True, key
        else:
            assert resolved["embeddable"] is False, key


def test_a_third_party_tool_is_never_embeddable_whatever_the_yaml_says(app):
    assert "third_party_tool" not in external.EMBEDDABLE_TYPES
    assert "third_party_content" not in external.EMBEDDABLE_TYPES


def test_the_rendered_launch_point_carries_the_pivot_and_no_key(app):
    m = content.get_mission(cd(app), SLUG)
    payload = content.render_mission(m, "en", cd(app))
    external_cards = [a for a in payload["artifacts"] if a["external"]]
    assert external_cards, "mission 1 has launch points"
    for card in external_cards:
        assert card["external"]["tool_name"]
        assert card["external"]["expected_pivot"]

    banned = {"validator", "accept", "correct",
              "fallback_snapshot", "availability_check", "path"}

    def walk(node, where=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, f"{where}.{key}"
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")

    walk(payload)


def test_render_without_a_content_dir_carries_no_launch_point(app):
    """Every existing caller keeps working, and gets a purely local mission."""
    m = content.get_mission(cd(app), SLUG)
    payload = content.render_mission(m, "en")
    assert all(a["external"] is None for a in payload["artifacts"])


ANSWER_IN_THE_COMMIT_SNAPSHOT = "ALLOW_UNSIGNED_RECOVERY"


def test_a_published_launch_point_shows_the_brief_and_not_the_file(app, event,
                                                                  facilitator):
    """State 1 — external, ready, normal mode."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/commit").data.decode()

    assert "Open externally" in body
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body
    assert "https://github.com/sora-dev77/sakura-secureconnect-client" in body
    assert 'data-stuck' in body
    assert "data-rung=" in body

    assert ANSWER_IN_THE_COMMIT_SNAPSHOT not in body
    assert "UPDATE_CHANNEL_URL" not in body
    assert "<pre" not in body
    assert "renderJSON" not in body


def test_there_is_no_offline_mode_to_declare(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")

    console = facilitator.get("/facilitator/").data.decode()
    assert 'data-flag="fallback_snapshots"' not in console

    payload = facilitator.get("/facilitator/api/progress").get_json()
    assert "fallback_snapshots" not in payload["session"]
    assert "offline_mode" not in payload["status"]

    facilitator.post("/facilitator/session/state", {"fallback_snapshots": True})
    body = p.get("/artifacts/commit").data.decode()
    assert "Offline mode" not in body
    assert ANSWER_IN_THE_COMMIT_SNAPSHOT not in body
    assert "Open externally" in body


def test_an_unpublished_launch_point_shows_the_file(app, event, facilitator,
                                                    monkeypatch):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")

    real = external.load(cd(app))
    pending = dict(real)
    pending["m1_repository"] = dict(real["m1_repository"], status="pending", url=None)
    monkeypatch.setattr(external, "load", lambda content_dir, force=False: pending)

    body = p.get("/artifacts/commit").data.decode()
    assert "Not published" in body
    assert ANSWER_IN_THE_COMMIT_SNAPSHOT in body


def test_the_last_two_sources_are_published_and_serve_nothing(app, event,
                                                              facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")

    for artifact_id, tell in (("username-collision", "Mori Frames"),
                              ("token-audit", "PAT-7f31c0")):
        body = p.get(f"/artifacts/{artifact_id}").data.decode()
        assert "Open externally" in body, artifact_id
        assert "Not published" not in body, artifact_id
        assert tell not in body, f"{artifact_id} still prints its own evidence"


def test_the_server_never_reads_the_file_it_is_not_showing(app):
    from app.artifacts import artifact_brief

    m = content.get_mission(cd(app), SLUG)
    entry = content.find_artifact(m, "commit")
    brief = artifact_brief(entry, "en")
    assert brief["body"] is None
    assert brief["data"] is None
    assert brief["withheld"] is True
    assert "sha256" not in brief


def test_no_launch_point_can_be_switched_to_its_saved_copy(app, event,
                                                          facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    facilitator.post("/facilitator/session/state", {"fallback_snapshots": True})

    for artifact in ("commit", "github-account", "dev-notes", "photo-exif"):
        body = p.get(f"/artifacts/{artifact}").data.decode()
        assert "Offline mode" not in body, artifact
        assert "Open externally" in body, artifact
        assert p.get(f"/artifacts/{artifact}/download").status_code == 409, artifact


def test_grading_reads_the_authored_key_wherever_the_team_read_the_source(
        app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    facilitator.post(f"/facilitator/mission/{SLUG}/close", {})

    from app.scoring import team_breakdown
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    assert team_breakdown(team)["by_category"]["correctness"] > 0


def test_a_source_url_is_stored_and_returned_to_the_team(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account",
                             "source_url": "https://example.test/sora_dev77",
                             "excerpt": "the profile line"})
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    link = state["answers"]["m1_key_id"]["evidence"][0]
    assert link["source_url"] == "https://example.test/sora_dev77"


@pytest.mark.parametrize("hostile", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "  JAVASCRIPT:alert(1)",
    "file:///etc/passwd",
    "not a url at all",
])
def test_only_http_urls_are_kept(app, event, facilitator, hostile):
    """The facilitator clicks these links while grading."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account", "source_url": hostile,
                             "excerpt": "x"})
    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    assert state["answers"]["m1_key_id"]["evidence"][0]["source_url"] is None


def test_the_artifact_page_names_no_question_at_all(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/commit").data.decode()

    assert 'id="captarget"' not in body
    m = content.get_mission(cd(app), SLUG)
    graded = [q for q in m["questions"] if q.get("evidence_points")]
    assert graded
    for q in graded:
        assert f'value="{q["key"]}"' not in body, q["key"]


def test_capture_claims_nothing_about_archiving(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/commit").data.decode().lower()

    for claim in ("archiv", "preserv", "forensic", "tamper"):
        for line in body.splitlines():
            if claim in line and "does not save or preserve" not in line:
                raise AssertionError(f"capture claims too much: {line.strip()[:120]}")


def test_a_question_with_no_evidence_points_is_not_a_capture_target(app, event,
                                                                   facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    body = p.get("/artifacts/commit").data.decode()
    m = content.get_mission(cd(app), SLUG)
    for q in m["questions"]:
        if not q.get("evidence_points"):
            assert f'value="{q["key"]}"' not in body, q["key"]


def test_no_optional_resource_is_referenced_by_a_mission(app):
    """Optional has to mean optional: nothing graded may point at one."""
    missions = list(content.load_missions(cd(app)).values())
    referenced = set()
    for m in missions:
        for a in m.get("artifacts") or []:
            key = external.placeholder_key(a.get("external_url") or "")
            if key:
                referenced.add(key)
    for key, entry in external.load(cd(app)).items():
        if entry.get("optional"):
            assert key not in referenced, key


def test_the_graph_is_empty_until_the_team_attaches_evidence(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    graph = p.get("/api/investigation-graph").get_json()
    assert graph["edges"] == [] and graph["sources"] == 0


def test_the_graph_draws_what_the_team_attached(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account",
                             "source_url": "https://example.test/x",
                             "excerpt": "the profile line"})
    graph = p.get("/api/investigation-graph").get_json()
    assert graph["sources"] == 1 and graph["findings"] == 1
    edge = graph["edges"][0]
    assert edge["src"] == "a:github-account" and edge["dst"] == "q:m1_key_id"
    assert edge["url"] == "https://example.test/x"


def test_the_right_hand_side_is_the_teams_claim(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account", "excerpt": "x"})

    graph = p.get("/api/investigation-graph").get_json()
    finding = next(n for n in graph["nodes"] if n["id"] == "q:m1_key_id")
    assert finding["answer"] == "SR-REL-2019"
    assert finding["label"] and finding["label"] != finding["answer"]


def test_the_graph_is_team_scoped(app, event, facilitator):
    """Same invariant as every other read here: the cookie's team, only."""
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    a = join(app, event, "Team 1", "aoi")
    b = join(app, event, "Team 2", "ren")
    a.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    a.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account", "excerpt": "mine"})

    assert a.get("/api/investigation-graph").get_json()["edges"]
    assert b.get("/api/investigation-graph").get_json()["edges"] == []


def test_the_graph_never_names_an_answer(app, event, facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})
    wind_forward(app, event, SLUG)
    open_all_sources(app, event, SLUG)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/submission", {"mission_slug": SLUG, "question_key": "m1_key_id",
                               "answer": "SR-REL-2019"})
    p.post("/api/evidence", {"mission_slug": SLUG, "question_key": "m1_key_id",
                             "artifact_id": "github-account", "excerpt": "x"})
    blob = p.get("/api/investigation-graph").data.decode()
    for banned in ("validator", "accept", "accepted_evidence"):
        assert banned not in blob


def test_the_added_columns_exist_on_a_fresh_database(app):
    from sqlalchemy import inspect

    from app import _ADDED_COLUMNS

    inspector = inspect(db.engine)
    for table, column, _spec in _ADDED_COLUMNS:
        have = {c["name"] for c in inspector.get_columns(table)}
        assert column in have, f"{table}.{column}"
