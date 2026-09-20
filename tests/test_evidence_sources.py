import csv
import pathlib

import pytest

from .conftest import join

SLUG = "threat-intelligence"
ART = pathlib.Path(__file__).resolve().parents[1] / "artifacts" / "threat-intelligence"

UPLOAD_AT = "2026-09-02T00:42:19Z"
EXECUTED_AT = "2026-09-02T00:19:16Z"


def open_mission(facilitator):
    facilitator.post(f"/facilitator/mission/{SLUG}/open", {})


def cite(p, question, artifact, excerpt="read here"):
    """Attach evidence exactly as the picker does."""
    return p.post("/api/evidence", {"mission_slug": SLUG,
                                    "question_key": question,
                                    "artifact_id": artifact,
                                    "excerpt": excerpt})


def test_the_proxy_log_really_records_the_upload_time():
    text = (ART / "proxy.log").read_text(encoding="utf-8")
    line = [l for l in text.splitlines() if UPLOAD_AT.rstrip("Z") in l or
            "00:42:19" in l]
    assert line, "proxy.log no longer carries the upload"
    assert "status=201" in line[0]
    assert "method=PUT" in line[0]


def test_the_network_log_records_the_same_upload_second():
    rows = list(csv.DictReader(
        (ART / "network_connections.csv").read_text(encoding="utf-8").splitlines()))
    hit = [r for r in rows if r["timestamp_utc"] == UPLOAD_AT]
    assert hit, "network_connections.csv no longer carries the upload second"
    assert hit[0]["host"] == "SR-DEV-077"
    assert hit[0]["destination"] == "archive.sakura-vpn-update.com"
    assert hit[0]["bytes_out"] == "6928384"

    proxy = (ART / "proxy.log").read_text(encoding="utf-8")
    assert "6928384" in proxy, "the two sources no longer agree on the size"


def test_the_file_timeline_really_records_the_execution(app):
    rows = list(csv.DictReader(
        (ART / "file_timeline.csv").read_text(encoding="utf-8").splitlines()))
    starts = [r for r in rows
              if r["event"] == "process_start" and r["host"] == "SR-DEV-077"]
    assert len(starts) == 1, starts
    assert starts[0]["timestamp_utc"] == EXECUTED_AT
    assert starts[0]["path"].endswith("SakuraVPNUpdate_4.2.1.exe")
    assert starts[0]["sha256"].startswith("ff749df7")
    assert starts[0]["source"] == "edr"

    downloads = [r for r in rows
                 if r["event"] == "download_complete" and r["host"] == "SR-DEV-077"]
    assert downloads and downloads[0]["timestamp_utc"] != EXECUTED_AT


@pytest.mark.parametrize("artifact", ["proxy-log", "network"])
def test_both_sources_are_accepted_for_the_upload_time(app, event, facilitator,
                                                       artifact):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    r = cite(p, "m3_upload_time", artifact, f"upload complete at {UPLOAD_AT}")
    assert r.status_code == 200, (artifact, r.status_code, r.get_json())
    assert r.get_json().get("ok") is True


@pytest.mark.parametrize("artifact", ["file-timeline", "process-tree"])
def test_both_sources_are_accepted_for_the_execution_time(app, event,
                                                          facilitator, artifact):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    r = cite(p, "m3_execution_time", artifact, f"process_start {EXECUTED_AT}")
    assert r.status_code == 200, (artifact, r.status_code, r.get_json())
    assert r.get_json().get("ok") is True


def test_an_artifact_that_is_not_in_this_mission_is_refused(app, event,
                                                            facilitator):
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    for bad in ("process", "nope", "commit"):     # `commit` belongs to M1
        r = cite(p, "m3_upload_time", bad)
        assert r.status_code == 400, (bad, r.status_code)


@pytest.mark.parametrize("question,artifact", [
    ("m3_upload_time", "mitre"),
    ("m3_upload_time", "virustotal"),
    ("m3_execution_time", "mitre"),
    ("m3_upload_time", "persistence"),
])
def test_an_irrelevant_source_is_accepted_but_scores_nothing(app, event,
                                                             facilitator,
                                                             question, artifact):
    from app import missions as content
    from app.scoring import check_evidence
    from app.models import Submission

    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    r = cite(p, question, artifact)
    assert r.status_code == 200, "the picker takes any artifact of the mission"

    definition = content.get_mission(app.config["CONTENT_DIR"], SLUG)
    q = content.find_question(definition, question)
    sub = Submission.query.filter_by(question_key=question).one()
    assert check_evidence(q, sub) is False, (question, artifact)


@pytest.mark.parametrize("question,artifact", [
    ("m3_upload_time", "proxy-log"),
    ("m3_upload_time", "network"),
    ("m3_execution_time", "file-timeline"),
    ("m3_execution_time", "process-tree"),
])
def test_a_source_that_carries_the_fact_scores(app, event, facilitator,
                                               question, artifact):
    from app import missions as content
    from app.scoring import check_evidence
    from app.models import Submission

    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    assert cite(p, question, artifact).status_code == 200

    definition = content.get_mission(app.config["CONTENT_DIR"], SLUG)
    q = content.find_question(definition, question)
    sub = Submission.query.filter_by(question_key=question).one()
    assert check_evidence(q, sub) is True, (question, artifact)


def test_the_accepted_lists_only_name_artifacts_that_exist(app):
    """A typo in an accepted list is a 400 nobody can explain on the day."""
    from app import missions as content

    cd = app.config["CONTENT_DIR"]
    for slug in ("digital-footprint", "fake-infrastructure",
                 "threat-intelligence"):
        definition = content.get_mission(cd, slug)
        ids = {a["id"] for a in definition["artifacts"]}
        for q in definition["questions"]:
            for accepted in (q.get("accepted_evidence") or []):
                assert accepted in ids, f"{slug}:{q['key']} -> {accepted}"


def test_the_evidence_is_readable_back_after_it_is_accepted(app, event,
                                                            facilitator):
    """Accepted is not enough; it has to survive to the team's own state."""
    open_mission(facilitator)
    p = join(app, event, "Team 1", "kenji")
    cite(p, "m3_upload_time", "network", f"upload complete at {UPLOAD_AT}")

    state = p.get(f"/api/mission-state?mission_slug={SLUG}").get_json()
    saved = state["answers"]["m3_upload_time"]["evidence"]
    assert saved and saved[0]["artifact_id"] == "network"
    assert UPLOAD_AT in saved[0]["excerpt"]
