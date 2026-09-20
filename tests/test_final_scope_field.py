from app.models import FinalReport

from .conftest import join

SCOPE = "SR-DEV-077 compromised; SR-FIN-014 download only; SR-LAB-021 contact only."


def _open_final(facilitator):
    facilitator.post("/facilitator/session/state", {"final_open": True})


def test_the_form_has_a_box_for_it(app, event, facilitator):
    _open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    html = p.get("/final").get_data(as_text=True)

    assert 'id="scope"' in html
    assert "Scope — which hosts were affected" in html
    assert "SR-FIN-014" in html


def test_it_is_saved_and_read_back(app, event, facilitator):
    _open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    r = p.post("/api/final", {"scope": SCOPE})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]

    from app.models import Team
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    row = FinalReport.query.filter_by(team_id=team.id).one()
    assert row.scope == SCOPE
    assert SCOPE in p.get("/final").get_data(as_text=True)


def test_the_facilitator_reads_it_when_grading(app, event, facilitator):
    _open_final(facilitator)
    p = join(app, event, "Team 1", "kenji")
    p.post("/api/final", {"scope": SCOPE})

    from app.models import Team
    team = Team.query.filter_by(session_id=event.id, display_name="Team 1").one()
    html = facilitator.get(f"/facilitator/grade/{team.id}").get_data(as_text=True)
    assert SCOPE in html
